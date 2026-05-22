# Restaurant Map

Interactive map of restaurants tracked in `Restaurant Tracker ...csv`. Markers are color-coded by visited status; clicking a marker opens a popup with all captured details (cuisine, price, rating, comment, URL).

Open-source stack — no billing, just one free sign-up:
- **[Leaflet](https://leafletjs.com/)** — map rendering (BSD-2)
- **[OpenStreetMap](https://www.openstreetmap.org/)** — map tiles (ODbL)
- **[LocationIQ](https://locationiq.com/)** — geocoding (free tier: 5,000 req/day, no credit card)

## Architecture

```
Notion DB ──► sync_notion.py ──┐
                               ├──► geocode_rows() ──► restaurants.json ──► index.html
CSV (historical) ──► geocode.py ┘                                              (Leaflet + OSM)
```

The source of truth is your Notion database. `sync_notion.py` pulls every row, hands it to the shared `geocode_rows()` function in `geocode.py`, and writes `restaurants.json`. The CSV path still works as a fallback (`python geocode.py`).

## Setup

### 1. Python environment

```bash
cd /home/mathhur_harssh/projects/restaurants
python3 -m venv .venv
source .venv/bin/activate
pip install requests python-dotenv
```

### 2. Get a LocationIQ key

1. Sign up at <https://locationiq.com/register> (email only, no credit card).
2. Verify your email; the dashboard shows your **Access Token**.
3. Configure the script:
   ```bash
   cp .env.example .env
   # paste your access token into .env as LOCATIONIQ_API_KEY=...
   ```

### 3. Connect Notion (recommended)

This makes Notion the source of truth — add rows there and re-sync, no CSV editing.

1. Create an integration at <https://www.notion.so/profile/integrations> → **+ New integration** → Internal Integration → pick your workspace.
2. Copy the **Internal Integration Secret** (looks like `ntn_…` or `secret_…`).
3. Open your restaurants database in Notion → top-right `…` → **Connections** → search the integration name → **Confirm**.
4. Add to `.env`:
   ```
   NOTION_TOKEN=ntn_xxxxxxxxxxxx
   NOTION_DATABASE_ID=193b17183004802f8ab0d1f5ca29ebfd
   ```
5. Confirm the database schema matches the expected mapping (one-shot check):
   ```bash
   python discover_notion_schema.py
   ```
   It prints each Notion property with its type. The expected properties are: `Restaurant Name`, `City`, `Comment`, `Cuisine Type`, `Price`, `Rating`, `URL`, `Visited`. If any are missing or renamed in your DB, edit the `PROP_*` constants at the top of `sync_notion.py`.

### 4. Run the sync

```bash
python sync_notion.py
```

This pulls every row from Notion, runs them through the geocoder, and writes `restaurants.json`. The first run hits LocationIQ for every row (~2-3 minutes); subsequent runs are near-instant for unchanged rows (`.cache/` keyed by name+city).

Output:
- `restaurants.json` — the geocoded data the map reads (commit this)
- `skipped.json` — rows that were filtered (non-venues) or that the geocoder couldn't locate
- `.cache/geocode_cache.json` — per-row cache (gitignored)

#### Fallback: CSV-only workflow

If you'd rather skip Notion and edit the CSV directly:

```bash
python geocode.py
```

This reads `Restaurant Tracker ...csv` and writes the same `restaurants.json`.

### 5. Preview locally

```bash
python3 -m http.server 8000
# open http://localhost:8000
```

### 6. Deploy to GitHub Pages

```bash
git init
git add .
git commit -m "Initial restaurant map"
gh repo create restaurants --public --source=. --push
# Then: GitHub repo → Settings → Pages → Source: main branch / root
```

Your map will be live at `https://<your-github-username>.github.io/restaurants/`.

## Fully cloud-hosted setup (recommended)

Skip running anything locally — let GitHub Actions sync Notion → live site + Google My Maps on a daily schedule (and on-demand). After ~20 min of one-time setup, you only edit in Notion.

```
You edit in Notion (any device)
        │
        ▼  daily 3am UTC + manual button
┌──────────────────────────────────────────────────┐
│  GitHub Actions: .github/workflows/sync.yml      │
│   1. sync_notion.py  → restaurants.json          │
│   2. to_sheets.py    → Google Sheet              │
│   3. commit + push restaurants.json              │
└──────────────────────────────────────────────────┘
        │                              │
        ▼                              ▼
  yearof2022.github.io           Google Sheet
  (auto-redeploys)                     │
                                       ▼
                            Google My Maps layer
                            (one-click "Refresh data")
```

### 1. Google Cloud + Sheets API (~10 min)

1. Open <https://console.cloud.google.com> → **New Project** → name it (e.g. `restaurant-sync`).
2. **APIs & Services → Library** → search "Google Sheets API" → **Enable**.
3. **APIs & Services → Credentials** → **Create credentials → Service account**. Give it any name; skip the optional permission steps.
4. On the service account row, click ⋮ → **Manage keys** → **Add key → Create new key → JSON**. Download the file. Treat it like a password.

### 2. Create the destination Sheet (~2 min)

1. Open <https://sheets.google.com> → blank sheet → name it **"Restaurant Map (auto-synced)"**.
2. Copy the **spreadsheet ID** from the URL: `https://docs.google.com/spreadsheets/d/<THIS_PART>/edit`.
3. Click **Share** → paste the service account's email (`client_email` in the JSON file, looks like `…@<project>.iam.gserviceaccount.com`) → set **Editor** → uncheck "Notify".

### 3. Add GitHub secrets (~3 min)

On your repo → **Settings → Secrets and variables → Actions → New repository secret**, add five secrets:

| Secret | Value |
|---|---|
| `NOTION_TOKEN` | from your `.env` |
| `NOTION_DATABASE_ID` | from your `.env` |
| `LOCATIONIQ_API_KEY` | from your `.env` |
| `GOOGLE_SHEETS_CREDENTIALS` | entire JSON content from step 1.4 (paste raw) |
| `GOOGLE_SHEETS_ID` | spreadsheet ID from step 2.2 |

### 4. Trigger the first run (~2 min)

GitHub → **Actions** tab → **Sync Notion → Sheet** → **Run workflow**. After ~90 sec the run completes; open the Sheet — it should have 12 columns and ~200 data rows.

### 5. First My Maps import (~5 min, one-time)

1. Open <https://www.google.com/mymaps> → **+ Create a new map** → rename it.
2. Default layer (top-left panel) → **Import** → **Google Drive** tab → pick the sheet from step 2.
3. **Position your placemarks**: tick `Latitude` + `Longitude` → Continue.
4. **Title your markers**: pick `Name` → Finish.
5. Click the **paint roller** on the layer → **Style by data column** → `Visited` → set Yes = green, No = red.
6. View on phone: **Google Maps app** → profile photo → **Your places** → **Maps** tab → your new map.

### 6. Daily life

| When | What to do |
|---|---|
| You edit something in Notion | Nothing. Tomorrow morning the cron updates the site and the Sheet. |
| You want updates **now** | GitHub → **Actions → Run workflow** → wait ~90 sec → in My Maps, click the layer's ⋮ → **Refresh data**. |
| You add new venues | Same — automatic on the next cron, or trigger manually. |

### Local dev (optional)

You can still run things locally:

```bash
mkdir -p .cache
mv ~/Downloads/<service-account>.json .cache/google-credentials.json
# add GOOGLE_SHEETS_ID=<your-sheet-id> to .env
python sync_notion.py && python to_sheets.py
```

`.cache/` is gitignored, so the credentials never leave your machine.

## Updating the data (manual / local-only)

1. Add a row in your Notion database.
2. Run `python sync_notion.py` — cached rows skip, only the new row hits LocationIQ (~1 sec).
3. Commit `restaurants.json` and push.

(Or with the CSV fallback: edit the CSV, run `python geocode.py`, commit.)

## Notes

- Rows whose names look like list/reel bookmarks (e.g. `Best Desserts 1`, `Restaurants in Mumbai`) are skipped automatically. They land in `skipped.json` with reason `"non_venue"`.
- Rows the geocoder couldn't locate go to `skipped.json` with reason `"not_found"`. Nominatim is less forgiving than Google with fuzzy restaurant names; adding a neighborhood (`Restaurant Name, Powai`) usually fixes it.
- For heavy production use (more than personal), consider self-hosting Nominatim or switching to a commercial provider like LocationIQ, Mapbox, or Stadia Maps — the same `geocode.py` shape works with minor URL changes.
- Tile usage: this site uses OSM's standard tile server, which is fine for personal/low-volume use. For higher traffic, swap the `L.tileLayer(...)` URL in `app.js` for [Stadia](https://stadiamaps.com/), [CARTO](https://carto.com/basemaps/), or another provider.
