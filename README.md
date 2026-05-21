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

## Updating the data

1. Add a row in your Notion database.
2. Run `python sync_notion.py` — cached rows skip, only the new row hits LocationIQ (~1 sec).
3. Commit `restaurants.json` and push.

(Or with the CSV fallback: edit the CSV, run `python geocode.py`, commit.)

## Notes

- Rows whose names look like list/reel bookmarks (e.g. `Best Desserts 1`, `Restaurants in Mumbai`) are skipped automatically. They land in `skipped.json` with reason `"non_venue"`.
- Rows the geocoder couldn't locate go to `skipped.json` with reason `"not_found"`. Nominatim is less forgiving than Google with fuzzy restaurant names; adding a neighborhood (`Restaurant Name, Powai`) usually fixes it.
- For heavy production use (more than personal), consider self-hosting Nominatim or switching to a commercial provider like LocationIQ, Mapbox, or Stadia Maps — the same `geocode.py` shape works with minor URL changes.
- Tile usage: this site uses OSM's standard tile server, which is fine for personal/low-volume use. For higher traffic, swap the `L.tileLayer(...)` URL in `app.js` for [Stadia](https://stadiamaps.com/), [CARTO](https://carto.com/basemaps/), or another provider.
