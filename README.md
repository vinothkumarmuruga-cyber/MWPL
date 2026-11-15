# NSE OI / MWPL Tracker (Streamlit)

Date-wise OI change for all NSE F&O symbols + latest MWPL / limit table.

## Deploy
1. GitHub -> New repository `nse-oi-tracker` -> upload everything in this folder (keep the `data/` folder).
2. https://share.streamlit.io -> New app -> pick the repo, branch `main`, main file `app.py` -> Deploy.
3. (Optional, to save new days permanently) App -> Settings -> Secrets -> paste contents of
   `.streamlit/secrets.toml.example` with your GitHub token and repo name.

## Daily use
Open the app -> sidebar -> **Fetch from NSE** (or upload the zip) -> **Save new days to GitHub**.
Without the token, new days live only until the app restarts; add the CSVs from `data/` to the repo manually instead.
