import base64, io, re, zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="NSE OI / MWPL Tracker", layout="wide")
DATA_DIR = Path(__file__).parent / "data"
URL = "https://nsearchives.nseindia.com/archives/nsccl/mwpl/combineoi_{d}.zip"
COLS = ["NSE Symbol", "MWPL", "Open Interest", "Limit for Next Day"]
HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Referer": "https://www.nseindia.com/"}

# ---------------------------------------------------------------- data layer
@st.cache_data(show_spinner=False)
def load_repo_days(_sig):
    out = {}
    for f in DATA_DIR.glob("combineoi_*.csv"):
        m = re.search(r"(\d{8})", f.name)
        if m:
            out[datetime.strptime(m.group(1), "%d%m%Y").date()] = pd.read_csv(f)
    return out

def parse_zip(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        df = pd.read_csv(io.BytesIO(z.read(name)))
    df.columns = [str(c).strip() for c in df.columns]
    df["NSE Symbol"] = df["NSE Symbol"].astype(str).str.strip()
    return df[COLS]

def fetch_day(d: date):
    s = requests.Session(); s.headers.update(HEAD)
    try: s.get("https://www.nseindia.com", timeout=15)
    except Exception: pass
    r = s.get(URL.format(d=f"{d:%d%m%Y}"), timeout=40)
    return r.content if r.status_code == 200 and r.content[:2] == b"PK" else None

def github_save(d: date, df: pd.DataFrame):
    tok, repo = st.secrets["GITHUB_TOKEN"], st.secrets["GITHUB_REPO"]
    branch = st.secrets.get("GITHUB_BRANCH", "main")
    path = f"data/combineoi_{d:%d%m%Y}.csv"
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    sha = requests.get(api, headers=h, params={"ref": branch}).json().get("sha")
    body = {"message": f"Add OI data {d:%d-%m-%Y}", "branch": branch,
            "content": base64.b64encode(df.to_csv(index=False).encode()).decode()}
    if sha: body["sha"] = sha
    return requests.put(api, headers=h, json=body).status_code in (200, 201)

# ---------------------------------------------------------------- state
if "new" not in st.session_state: st.session_state.new = {}
days = dict(load_repo_days(len(list(DATA_DIR.glob("*.csv")))))
days.update(st.session_state.new)

# ---------------------------------------------------------------- sidebar: add data
with st.sidebar:
    st.header("Add data")
    ups = st.file_uploader("Upload NSE combineoi zip(s)", type="zip", accept_multiple_files=True)
    if ups:
        for u in ups:
            m = re.search(r"(\d{8})", u.name)
            if m:
                d = datetime.strptime(m.group(1), "%d%m%Y").date()
                if d not in st.session_state.new:
                    st.session_state.new[d] = parse_zip(u.getvalue())
    st.divider()
    c1, c2 = st.columns(2)
    d_from = c1.date_input("From", date.today() - timedelta(days=1), format="DD/MM/YYYY")
    d_to = c2.date_input("To", date.today(), format="DD/MM/YYYY")
    if st.button("Fetch from NSE", width="stretch"):
        d, got, miss = d_from, 0, []
        while d <= d_to:
            if d.weekday() < 5 and d not in days:
                try: c = fetch_day(d)
                except Exception: c = None
                if c: st.session_state.new[d] = parse_zip(c); got += 1
                else: miss.append(f"{d:%d-%m}")
            d += timedelta(1)
        st.success(f"Fetched {got} day(s)")
        if miss: st.warning("Not available / blocked: " + ", ".join(miss) + ". Upload zips manually.")
        st.rerun()
    if st.session_state.new:
        st.divider()
        st.caption(f"{len(st.session_state.new)} unsaved day(s) (lost on restart)")
        if "GITHUB_TOKEN" in st.secrets and st.button("Save new days to GitHub", width="stretch"):
            ok = sum(github_save(d, df) for d, df in st.session_state.new.items())
            st.success(f"Committed {ok}/{len(st.session_state.new)} file(s). App redeploys shortly.")
        elif "GITHUB_TOKEN" not in st.secrets:
            st.caption("Add GITHUB_TOKEN / GITHUB_REPO in app Secrets to save permanently.")

st.title("NSE OI / MWPL Tracker")
if len(days) < 1:
    st.info("No data yet. Upload zips or fetch from NSE in the sidebar."); st.stop()

# ---------------------------------------------------------------- OI change table
dates = sorted(days)
oi = pd.concat({d: days[d].drop_duplicates("NSE Symbol").set_index("NSE Symbol")["Open Interest"]
                for d in dates}, axis=1)
sign = st.radio("OI change", ["Previous day − Today  (your sheet; negative = OI rose)",
                              "Today − Previous day  (positive = OI rose)"], horizontal=True)
chg = pd.DataFrame(index=oi.index)
for prev, cur in zip(dates, dates[1:]):
    chg[cur] = (oi[prev] - oi[cur]) if sign.startswith("Previous") else (oi[cur] - oi[prev])
f1, f2, f3 = st.columns([2, 2, 1])
months = sorted({(d.year, d.month) for d in chg.columns}, reverse=True)
if months:
    msel = f1.selectbox("Month", months, format_func=lambda x: date(x[0], x[1], 1).strftime("%B %Y"))
    chg = chg[[c for c in chg.columns if (c.year, c.month) == msel]]
q = f2.text_input("Symbol filter", "").strip().upper()
if q: chg = chg[chg.index.str.contains(q, regex=False)]
newest_first = f3.checkbox("Newest date first", False)
chg = chg[sorted(chg.columns, reverse=newest_first)]
show = chg.copy(); show.columns = [f"{c:%d-%m-%Y}" for c in show.columns]
show.index.name = "NSE Symbol"

t1, t2 = st.tabs(["OI change (date-wise)", "Latest MWPL & limit"])
with t1:
    mx = float(show.abs().max().max()) if not show.empty else 0
    cmap = "RdYlGn_r" if sign.startswith("Previous") else "RdYlGn"   # green = OI rose
    st.caption("Green = OI increased, red = OI decreased")
    st.dataframe(show.style.format("{:,.0f}", na_rep="").background_gradient(
        cmap=cmap, axis=None, vmin=-mx, vmax=mx) if mx else show,
        width="stretch", height=650)
    buf = io.BytesIO(); show.to_excel(buf, sheet_name="OI change")
    st.download_button("Download as Excel", buf.getvalue(), "oi_change.xlsx")
with t2:
    last = dates[-1]
    L = days[last].copy()
    L["OI % of MWPL"] = (L["Open Interest"] / L["MWPL"] * 100).round(1)
    st.caption(f"Data for {last:%d-%m-%Y}  (limit shown applies to next trading day)")
    st.dataframe(L.sort_values("OI % of MWPL", ascending=False), width="stretch",
                 hide_index=True, height=650)
