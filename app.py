import base64, io, re, zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="NSE OI / MWPL Tracker", layout="wide")
DATA_DIR = Path(__file__).parent / "data"
URL = "https://nsearchives.nseindia.com/archives/nsccl/mwpl/combineoi_{d}.zip"
COLS = ["NSE Symbol", "MWPL", "Open Interest", "Future Equivalent OI", "Limit for Next Day"]
HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Referer": "https://www.nseindia.com/"}

# ---------------------------------------------------------------- data layer
def clean(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    if "Future Equivalent OI" not in df: df["Future Equivalent OI"] = np.nan
    df["NSE Symbol"] = df["NSE Symbol"].astype(str).str.strip()
    df["Limit for Next Day"] = df["Limit for Next Day"].astype(str).str.strip()
    return df[COLS].drop_duplicates("NSE Symbol")

@st.cache_data(show_spinner=False)
def load_repo_days(sig):
    out = {}
    for f in DATA_DIR.glob("combineoi_*.csv"):
        m = re.search(r"(\d{8})", f.name)
        if m: out[datetime.strptime(m.group(1), "%d%m%Y").date()] = clean(pd.read_csv(f))
    return out

def parse_zip(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        return clean(pd.read_csv(io.BytesIO(z.read(name))))

def fetch_day(d: date):
    s = requests.Session(); s.headers.update(HEAD)
    try: s.get("https://www.nseindia.com", timeout=15)
    except Exception: pass
    r = s.get(URL.format(d=f"{d:%d%m%Y}"), timeout=40)
    return r.content if r.status_code == 200 and r.content[:2] == b"PK" else None

def github_save(d: date, df: pd.DataFrame):
    tok, repo = st.secrets["GITHUB_TOKEN"], st.secrets["GITHUB_REPO"]
    branch = st.secrets.get("GITHUB_BRANCH", "main")
    api = f"https://api.github.com/repos/{repo}/contents/data/combineoi_{d:%d%m%Y}.csv"
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    sha = requests.get(api, headers=h, params={"ref": branch}).json().get("sha")
    body = {"message": f"Add OI data {d:%d-%m-%Y}", "branch": branch,
            "content": base64.b64encode(df.to_csv(index=False).encode()).decode()}
    if sha: body["sha"] = sha
    return requests.put(api, headers=h, json=body).status_code in (200, 201)

def has_secret(k):
    try: return k in st.secrets
    except Exception: return False

# ---------------------------------------------------------------- state
if "new" not in st.session_state: st.session_state.new = {}
days = dict(load_repo_days(len(list(DATA_DIR.glob("*.csv")))))
days.update(st.session_state.new)
days = {d: clean(df) for d, df in days.items()}   # normalise old-format days kept in session

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Add data")
    ups = st.file_uploader("Upload NSE combineoi zip(s)", type="zip", accept_multiple_files=True)
    for u in ups or []:
        m = re.search(r"(\d{8})", u.name)
        if m:
            d = datetime.strptime(m.group(1), "%d%m%Y").date()
            if d not in st.session_state.new: st.session_state.new[d] = parse_zip(u.getvalue())
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
        if has_secret("GITHUB_TOKEN"):
            if st.button("Save new days to GitHub", width="stretch"):
                ok = sum(github_save(d, df) for d, df in st.session_state.new.items())
                st.success(f"Committed {ok}/{len(st.session_state.new)} file(s). App redeploys shortly.")
        else:
            st.caption("Add GITHUB_TOKEN / GITHUB_REPO in app Secrets to save permanently.")
    st.divider()
    st.header("Signal thresholds")
    t_buy = st.number_input("OI change % = notable", 1.0, 50.0, 5.0, 0.5)
    t_big = st.number_input("OI change % = strong", 1.0, 100.0, 10.0, 0.5)
    t_warn = st.number_input("MWPL used % = watch", 50.0, 100.0, 80.0, 1.0)
    t_near = st.number_input("MWPL used % = near ban", 50.0, 100.0, 90.0, 1.0)
    t_ban = st.number_input("MWPL used % = ban level", 50.0, 100.0, 95.0, 1.0)

st.title("NSE OI / MWPL Tracker")
if not days:
    st.info("No data yet. Upload zips or fetch from NSE in the sidebar."); st.stop()

dates = sorted(days)
oi = pd.concat({d: days[d].set_index("NSE Symbol")["Open Interest"] for d in dates}, axis=1)
fe = pd.concat({d: days[d].set_index("NSE Symbol")["Future Equivalent OI"] for d in dates}, axis=1)

def pct_change(prev, cur):
    return ((oi[cur] / oi[prev] - 1) * 100).replace([np.inf, -np.inf], np.nan)

t1, tD, t2, t3 = st.tabs(["OI change (date-wise)", "Decision (date-wise)", "Decision hints (latest day)", "Latest MWPL & limit"])

# ---------------------------------------------------------------- tab 1
def limit_pct(d):
    x = days[d].set_index("NSE Symbol")
    return pd.to_numeric(x["Limit for Next Day"], errors="coerce") / x["MWPL"] * 100   # "No Fresh Positions" -> blank

mp = pd.concat({d: limit_pct(d) for d in dates}, axis=1)

with t1:
    v1, v2, v3, v4 = st.columns([2, 2, 2, 1])
    view = v1.radio("Show", ["% change vs previous day", "Absolute change (contracts)",
                             "MWPL %  (Limit for next day ÷ MWPL × 100)",
                             "MWPL % change vs previous day (points)"])
    kind = ["pct", "abs", "mwpl", "mwpl_chg"][["%", "A", "M", "M"].index(view[0]) if view[0] != "M" else (2 if "÷" in view else 3)]
    if kind in ("pct", "abs", "mwpl_chg") and len(dates) < 2:
        st.info("Need at least 2 days of data.")
    else:
        if kind == "abs":
            sign = v2.radio("Sign", ["Previous − Today (negative = OI rose)", "Today − Previous (positive = OI rose)"])
        chg = pd.DataFrame(index=oi.index)
        if kind == "mwpl":
            for d in dates: chg[d] = mp[d]
        else:
            for prev, cur in zip(dates, dates[1:]):
                if kind == "pct": chg[cur] = pct_change(prev, cur)
                elif kind == "mwpl_chg": chg[cur] = mp[cur] - mp[prev]
                else: chg[cur] = (oi[prev] - oi[cur]) if sign.startswith("Previous") else (oi[cur] - oi[prev])
        months = sorted({(d.year, d.month) for d in chg.columns}, reverse=True)
        msel = v3.selectbox("Month", months, format_func=lambda x: date(x[0], x[1], 1).strftime("%B %Y"))
        chg = chg[[c for c in chg.columns if (c.year, c.month) == msel]]
        q = v3.text_input("Symbol filter", "").strip().upper()
        if q: chg = chg[chg.index.str.contains(q, regex=False)]
        newest = v4.checkbox("Newest first", True)
        chg = chg[sorted(chg.columns, reverse=newest)]
        show = chg.copy(); show.columns = [f"{c:%d-%m-%Y}" for c in show.columns]; show.index.name = "NSE Symbol"
        if show.empty:
            st.info("No rows.")
        else:
            if kind == "pct":
                st.caption("Green = OI rose, red = OI fell. Colour scale capped at ±20%.")
                sty = show.style.format("{:+.1f}%", na_rep="").background_gradient(cmap="RdYlGn", axis=None, vmin=-20, vmax=20)
            elif kind == "mwpl":
                st.caption("Limit for next day ÷ MWPL × 100 = how much of MWPL is still open for new positions. "
                           "Low (red) = crowded, near ban. Blank = 'No Fresh Positions' (in ban).")
                sty = show.style.format("{:.1f}%", na_rep="").background_gradient(cmap="RdYlGn", axis=None, vmin=0, vmax=100)
            elif kind == "mwpl_chg":
                st.caption("Change in MWPL % vs previous day, in percentage points. "
                           "Negative (red) = less room left = more OI built up. Positive (green) = room freed.")
                sty = show.style.format("{:+.1f}", na_rep="").background_gradient(cmap="RdYlGn", axis=None, vmin=-10, vmax=10)
            else:
                mx = float(show.abs().max().max()) or 1
                st.caption("Green = OI rose, red = OI fell.")
                sty = show.style.format("{:,.0f}", na_rep="").background_gradient(
                    cmap="RdYlGn_r" if sign.startswith("Previous") else "RdYlGn", axis=None, vmin=-mx, vmax=mx)
            st.dataframe(sty, width="stretch", height=650)
            buf = io.BytesIO(); show.to_excel(buf, sheet_name="OI change")
            st.download_button("Download as Excel", buf.getvalue(), "oi_change.xlsx")

# ---------------------------------------------------------------- tab 2
def build_hints():
    last, prev = dates[-1], dates[-2]
    L = days[last].set_index("NSE Symbol")
    df = pd.DataFrame({"OI": L["Open Interest"], "OI chg %": pct_change(prev, last)})
    first = dates[max(0, len(dates) - 6)]            # up to 5 sessions back
    df[f"OI chg % ({(last - first).days}d)"] = ((oi[last] / oi[first] - 1) * 100).replace([np.inf, -np.inf], np.nan) if first != last else np.nan
    df["MWPL used %"] = (L["Future Equivalent OI"] / L["MWPL"] * 100)
    df["MWPL % (limit÷MWPL)"] = pd.to_numeric(L["Limit for Next Day"], errors="coerce") / L["MWPL"] * 100
    df["Next-day limit"] = L["Limit for Next Day"]
    def hint(r):
        s, h = [], []
        lim = str(r["Next-day limit"]).lower()
        u, c = r["MWPL used %"], r["OI chg %"]
        if "no fresh" in lim:
            s.append("🔴 BAN"); h.append("In F&O ban: only square-off allowed, no fresh positions. Expect unwinding; avoid new entries until it exits the ban list")
        elif pd.notna(u) and u >= t_ban:
            s.append("🔴 BAN LEVEL"); h.append("At ban threshold: ban likely next session - avoid fresh positions")
        elif pd.notna(u) and u >= t_near:
            s.append("🟠 NEAR BAN"); h.append("Close to ban: fresh positions risky, expect squaring-off")
        elif pd.notna(u) and u >= t_warn:
            s.append("🟡 WATCH MWPL"); h.append("Crowded F&O stock: monitor daily")
        if pd.notna(c):
            if c >= t_big: s.append("🟢 STRONG BUILD-UP"); h.append("Big fresh positions added - check price direction: price up = long build-up, price down = short build-up")
            elif c >= t_buy: s.append("🟢 BUILD-UP"); h.append("OI rising - confirm with price")
            elif c <= -t_big: s.append("🔵 HEAVY UNWINDING"); h.append("Many positions closed - trend may be exhausting: price up = short covering, price down = long unwinding")
            elif c <= -t_buy: s.append("🔵 UNWINDING"); h.append("OI falling - move losing support")
        return pd.Series({"Signal": " + ".join(s) or "⚪ Neutral", "Hint": ". ".join(h) or "No notable OI move"})
    return pd.concat([df, df.apply(hint, axis=1)], axis=1), last, prev

with t2:
    if len(dates) < 2:
        st.info("Need at least 2 days of data.")
    else:
        H, last, prev = build_hints()
        st.caption(f"Latest {last:%d-%m-%Y} vs previous {prev:%d-%m-%Y}. OI alone shows participation, not direction - "
                   "always confirm with price: price↑+OI↑ long build-up · price↓+OI↑ short build-up · "
                   "price↑+OI↓ short covering · price↓+OI↓ long unwinding. Not investment advice.")
        f1, f2, f3 = st.columns([3, 2, 2])
        opts = ["🔴 BAN", "🔴 BAN LEVEL", "🟠 NEAR BAN", "🟡 WATCH MWPL", "🟢 STRONG BUILD-UP",
                "🟢 BUILD-UP", "🔵 HEAVY UNWINDING", "🔵 UNWINDING", "⚪ Neutral"]
        pick = f1.multiselect("Show signals", opts, default=[o for o in opts if o != "⚪ Neutral"])
        sortby = f2.selectbox("Sort by", ["OI chg % (biggest rise)", "OI chg % (biggest fall)", "MWPL used % (highest)", "Symbol"])
        q = f3.text_input("Symbol", "", key="hq").strip().upper()
        V = H[H["Signal"].apply(lambda s: any(p in s for p in pick))]
        if q: V = V[V.index.str.contains(q, regex=False)]
        V = {"OI chg % (biggest rise)": V.sort_values("OI chg %", ascending=False),
             "OI chg % (biggest fall)": V.sort_values("OI chg %"),
             "MWPL used % (highest)": V.sort_values("MWPL used %", ascending=False),
             "Symbol": V.sort_index()}[sortby]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("In ban / ban level", int(H["Signal"].str.contains("BAN").sum() - H["Signal"].str.contains("NEAR BAN").sum()))
        m2.metric("Near ban (≥%d%%)" % t_near, int(H["Signal"].str.contains("NEAR BAN").sum()))
        m3.metric("OI ≥ +%g%%" % t_buy, int(H["Signal"].str.contains("BUILD-UP").sum()))
        m4.metric("OI ≤ −%g%%" % t_buy, int(H["Signal"].str.contains("UNWINDING").sum()))
        cc = {c: st.column_config.NumberColumn(format="%+.1f%%") for c in V.columns if c.startswith("OI chg")}
        cc["MWPL used %"] = st.column_config.NumberColumn(format="%.1f%%")
        cc["MWPL % (limit÷MWPL)"] = st.column_config.NumberColumn(format="%.1f%%")
        cc["OI"] = st.column_config.NumberColumn(format="%d")
        st.dataframe(V, width="stretch", height=650, column_config=cc)

# ---------------------------------------------------------------- decision window (date-wise)
def decision_matrix():
    mw = pd.concat({d: days[d].set_index("NSE Symbol")["MWPL"] for d in dates}, axis=1)
    used = fe / mw * 100
    lim = pd.concat({d: days[d].set_index("NSE Symbol")["Limit for Next Day"] for d in dates}, axis=1)
    ban = lim.apply(lambda c: c.astype(str).str.lower().str.contains("no fresh"))
    cells, code = pd.DataFrame(index=oi.index), pd.DataFrame(index=oi.index)
    for i, d in enumerate(dates):
        p = pct_change(dates[i - 1], d) if i else pd.Series(np.nan, index=oi.index)
        def cell(sym):
            pc, u, b = p[sym], used.at[sym, d], ban.at[sym, d]
            if b: return "🔴 BAN"
            if pd.isna(pc): base = ""
            elif pc >= t_big: base = f"🟢🟢 {pc:+.1f}%"
            elif pc >= t_buy: base = f"🟢 {pc:+.1f}%"
            elif pc <= -t_big: base = f"🔵🔵 {pc:+.1f}%"
            elif pc <= -t_buy: base = f"🔵 {pc:+.1f}%"
            else: base = f"⚪ {pc:+.1f}%"
            risk = " 🔴" if u >= t_ban else " 🟠" if u >= t_near else " 🟡" if u >= t_warn else ""
            return (base + risk).strip()
        cells[d] = [cell(x) for x in oi.index]
        code[d] = np.where(p >= t_buy, 1, np.where(p <= -t_buy, -1, 0))
    streak = []
    for sym in oi.index:
        row, n = code.loc[sym].tolist()[::-1], 0
        if row and row[0] != 0:
            while n < len(row) and row[n] == row[0]: n += 1
            streak.append(f"{'🟢' if row[0] > 0 else '🔵'} {n}d {'build-up' if row[0] > 0 else 'unwinding'}")
        else: streak.append("")
    return cells, pd.Series(streak, index=oi.index, name="Streak")

def cell_color(v):
    v = str(v)
    if "BAN" in v or v.endswith("🔴"): return "background-color:#f8c9c4"
    if v.startswith("🟢🟢"): return "background-color:#7fd38a"
    if v.startswith("🟢"): return "background-color:#c9efce"
    if v.startswith("🔵🔵"): return "background-color:#8ab8ee"
    if v.startswith("🔵"): return "background-color:#d3e4fa"
    return ""

with tD:
    if len(dates) < 2:
        st.info("Need at least 2 days of data.")
    else:
        H, last, prev = build_hints()
        cells, streak = decision_matrix()
        st.caption("Each cell = OI % change vs previous day. 🟢 build-up (🟢🟢 strong) · 🔵 unwinding (🔵🔵 heavy) · ⚪ neutral · "
                   "🔴 BAN (no fresh positions) · risk dot after the % = MWPL used 🟡 watch / 🟠 near ban / 🔴 ban level. "
                   "Confirm direction with price before trading. Not investment advice.")
        g1, g2, g3, g4 = st.columns([2, 2, 2, 1])
        show_only = g1.selectbox("Latest day filter", ["All", "Risk (ban / near ban)", "Build-up", "Strong build-up", "Unwinding"])
        sortby = g2.selectbox("Sort by", ["Latest OI % (biggest rise)", "Latest OI % (biggest fall)", "MWPL used % (highest)", "Symbol"], key="ds")
        mset = sorted({(d.year, d.month) for d in dates}, reverse=True)
        msel = g3.selectbox("Month", mset, key="dm", format_func=lambda x: date(x[0], x[1], 1).strftime("%B %Y"))
        newest = g4.checkbox("Newest first", True, key="dn")
        q = st.text_input("Symbol filter", "", key="dq").strip().upper()
        dcols = sorted([d for d in dates if (d.year, d.month) == msel], reverse=newest)
        T = pd.concat([H[["Signal", "OI chg %", "MWPL used %"]].rename(columns={"Signal": "Latest signal"}),
                       streak, cells[dcols].rename(columns=lambda d: f"{d:%d-%m-%Y}")], axis=1)
        sig = T["Latest signal"]
        mask = {"All": sig.notna(), "Risk (ban / near ban)": sig.str.contains("BAN"), "Build-up": sig.str.contains("BUILD-UP"),
                "Strong build-up": sig.str.contains("STRONG"), "Unwinding": sig.str.contains("UNWINDING")}[show_only]
        T = T[mask]
        if q: T = T[T.index.str.contains(q, regex=False)]
        T = {"Latest OI % (biggest rise)": T.sort_values("OI chg %", ascending=False),
             "Latest OI % (biggest fall)": T.sort_values("OI chg %"),
             "MWPL used % (highest)": T.sort_values("MWPL used %", ascending=False),
             "Symbol": T.sort_index()}[sortby]
        T = T.rename(columns={"OI chg %": "OI % (latest)"}); T.index.name = "NSE Symbol"
        dnames = [c for c in T.columns if re.fullmatch(r"\d\d-\d\d-\d{4}", c)]
        mapper = T.style.map if hasattr(T.style, "map") else T.style.applymap
        sty = mapper(cell_color, subset=dnames).format({"OI % (latest)": "{:+.1f}%", "MWPL used %": "{:.1f}%"}, na_rep="")
        st.dataframe(sty, width="stretch", height=650)
        buf = io.BytesIO(); T.to_excel(buf, sheet_name="Decision")
        st.download_button("Download as Excel", buf.getvalue(), "decision_window.xlsx")

# ---------------------------------------------------------------- tab 3
with t3:
    last = dates[-1]; L = days[last].copy()
    L["MWPL used %"] = (L["Future Equivalent OI"] / L["MWPL"] * 100).round(1)
    L["MWPL % (limit÷MWPL)"] = (pd.to_numeric(L["Limit for Next Day"], errors="coerce") / L["MWPL"] * 100).round(1)
    st.caption(f"Data for {last:%d-%m-%Y}. MWPL used % = Future Equivalent OI / MWPL. Limit applies to next trading day.")
    st.dataframe(L.sort_values("MWPL used %", ascending=False), width="stretch", hide_index=True, height=650)
