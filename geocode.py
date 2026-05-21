#!/usr/bin/env python3
"""Geocode the restaurant CSV into restaurants.json via LocationIQ.

LocationIQ is a Nominatim-compatible service with a free tier (5k/day, 2 req/sec).
Sign up at https://locationiq.com/register and put your key in .env.

Run once after editing the CSV. Cached results live in .cache/geocode_cache.json
so re-runs only hit the API for new or changed rows.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv

ROOT = Path(__file__).parent
CSV_PATH = ROOT / "Restaurant Tracker 193b17183004815db7baf329c0555736_all.csv"
OUT_PATH = ROOT / "restaurants.json"
SKIPPED_PATH = ROOT / "skipped.json"
MANUAL_PATH = ROOT / "manual_coords.json"
CACHE_DIR = ROOT / ".cache"
CACHE_PATH = CACHE_DIR / "geocode_cache.json"

LOCATIONIQ_ENDPOINT = "https://us1.locationiq.com/v1/search"
RATE_LIMIT_SECONDS = 0.6  # free tier: 2 req/sec, leave headroom

# Bounding boxes (lng_min, lat_min, lng_max, lat_max) keep LocationIQ from
# matching a same-named place on the other side of the country.
CITY_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "Mumbai":     (72.75, 18.85, 73.10, 19.35),
    "Pune":       (73.65, 18.35, 74.05, 18.70),
    "Delhi":      (76.80, 28.35, 77.40, 28.90),
    "Bangalore":  (77.40, 12.80, 77.85, 13.20),
    "Nashik":     (73.60, 19.85, 74.00, 20.15),
    "Jamshedpur": (86.05, 22.65, 86.35, 22.95),
}

NON_VENUE_PATTERNS = [
    re.compile(r"^(Best|Party|New|Top|Must|Late Night|Karaoke|Chocolate Mousse|Biryani Spots|Rooftop)\b", re.I),
    re.compile(r"^(Restaurants|Party places|New restaurants|Best restaurants|Must visit)\s+in\b", re.I),
    re.compile(r"^Speakeasy Bars \d", re.I),
    re.compile(r"^[A-Za-z]+\s+in\s+(Bandra|Churchgate|Mumbai|Andheri|Pune|Delhi|BLR)$", re.I),
    re.compile(r"\bSpots\b", re.I),
]

MULTI_VENUE_NAMES = {"Cafe Churchill - Leopold - Cafe Universal"}


def is_non_venue(name: str) -> bool:
    if not name.strip():
        return True
    if name.strip() in MULTI_VENUE_NAMES:
        return True
    for pat in NON_VENUE_PATTERNS:
        if pat.search(name.strip()):
            return True
    return False


def load_manual_overrides() -> dict:
    """Load manual_coords.json. Returns {"name|city": {lat, lng}} for filled entries."""
    if not MANUAL_PATH.exists():
        return {}
    raw = json.loads(MANUAL_PATH.read_text())
    overrides = raw.get("overrides", {}) if isinstance(raw, dict) else {}
    result = {}
    for key, val in overrides.items():
        if not isinstance(val, dict):
            continue
        lat, lng = val.get("lat"), val.get("lng")
        if lat is None or lng is None:
            continue
        try:
            result[key.lower()] = {"lat": float(lat), "lng": float(lng)}
        except (TypeError, ValueError):
            continue
    return result


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def cache_key(name: str, city: str) -> str:
    return f"{name.strip().lower()}|{city.strip().lower()}"


def locationiq_search(api_key: str, query: str, verify_ssl: bool, city: str = "") -> dict | None:
    params = {"key": api_key, "q": query, "format": "json", "limit": 1}
    bounds = CITY_BOUNDS.get(city)
    if bounds:
        lng_min, lat_min, lng_max, lat_max = bounds
        # LocationIQ viewbox format: lon1,lat1,lon2,lat2 (top-left, bottom-right).
        params["viewbox"] = f"{lng_min},{lat_max},{lng_max},{lat_min}"
        params["bounded"] = 1
    try:
        resp = requests.get(LOCATIONIQ_ENDPOINT, params=params, timeout=20, verify=verify_ssl)
    except requests.RequestException as e:
        print(f"  ! network error: {e}", file=sys.stderr)
        return None
    if resp.status_code == 404:
        return None  # LocationIQ returns 404 when nothing matches
    if resp.status_code != 200:
        print(f"  ! HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return None
    try:
        results = resp.json()
    except ValueError:
        return None
    if not results:
        return None
    r = results[0]
    return {
        "place_id": str(r.get("place_id", "")),
        "lat": float(r["lat"]),
        "lng": float(r["lon"]),
        "formatted_address": r.get("display_name"),
    }


def geocode(api_key: str, name: str, city: str, verify_ssl: bool) -> dict | None:
    queries = [
        f"{name}, {city}, India" if city else f"{name}, India",
    ]
    if "," in name:
        head = name.split(",")[0].strip()
        if city:
            queries.append(f"{head}, {city}, India")

    for q in queries:
        result = locationiq_search(api_key, q, verify_ssl, city)
        time.sleep(RATE_LIMIT_SECONDS)
        if result:
            return result
    return None


def normalize_row(row: dict) -> dict:
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}


def geocode_rows(rows: list[dict]) -> tuple[int, int, int]:
    """Run the geocode pipeline over a pre-parsed list of row dicts.

    Each row must look like a CSV row: keys include "Restaurant Name", "City",
    and the other tracked fields. Writes restaurants.json + skipped.json as side
    effects. Returns (located, not_found, non_venue) counts.
    """
    load_dotenv(ROOT / ".env")

    api_key = os.environ.get("LOCATIONIQ_API_KEY")
    if not api_key or api_key == "your_key_here":
        raise RuntimeError(
            "LOCATIONIQ_API_KEY not set in .env. "
            "Get a free key at https://locationiq.com/register"
        )

    verify_ssl = os.environ.get("VERIFY_SSL", "0") == "1"
    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    cache = load_cache()
    manual = load_manual_overrides()
    located: list[dict] = []
    skipped: list[dict] = []

    if manual:
        print(f"Loaded {len(manual)} manual coordinate override(s) from {MANUAL_PATH.name}\n")

    for i, row in enumerate(rows, 1):
        name = row.get("Restaurant Name", "")
        city = row.get("City", "")

        if is_non_venue(name):
            skipped.append({**row, "_skip_reason": "non_venue"})
            continue

        # Manual override check — bypasses API and cache entirely
        manual_key = f"{name}|{city}".lower()
        if manual_key in manual:
            override = manual[manual_key]
            located.append({
                **row,
                "place_id": "manual",
                "lat": override["lat"],
                "lng": override["lng"],
                "formatted_address": "manual override",
            })
            continue

        key = cache_key(name, city)
        if key in cache:
            entry = cache[key]
            if entry.get("lat") is None:
                skipped.append({**row, "_skip_reason": "not_found"})
            else:
                located.append({**row, **entry})
            continue

        print(f"[{i:3d}/{len(rows)}] {name} — {city}")
        result = geocode(api_key, name, city, verify_ssl)
        cache[key] = result or {"place_id": None, "lat": None, "lng": None, "formatted_address": None}
        save_cache(cache)

        if result:
            located.append({**row, **result})
        else:
            skipped.append({**row, "_skip_reason": "not_found"})

    OUT_PATH.write_text(json.dumps(located, indent=2, ensure_ascii=False))
    SKIPPED_PATH.write_text(json.dumps(skipped, indent=2, ensure_ascii=False))

    n_non_venue = sum(1 for s in skipped if s.get("_skip_reason") == "non_venue")
    n_not_found = sum(1 for s in skipped if s.get("_skip_reason") == "not_found")
    print()
    print(f"✓ {len(located)} located → {OUT_PATH.name}")
    print(f"  {n_not_found} not found, {n_non_venue} skipped as non-venues → {SKIPPED_PATH.name}")
    return len(located), n_not_found, n_non_venue


def main() -> int:
    if not CSV_PATH.exists():
        print(f"ERROR: CSV not found at {CSV_PATH}", file=sys.stderr)
        return 1

    with CSV_PATH.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = [normalize_row(r) for r in reader]

    print(f"Loaded {len(rows)} rows from CSV.\n")

    try:
        geocode_rows(rows)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
