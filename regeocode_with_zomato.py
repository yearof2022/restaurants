#!/usr/bin/env python3
"""Re-geocode matched venues using their Zomato name (often includes area).

For each restaurant in restaurants.json that has a matched Zomato entry, build
a richer geocode query using the Zomato name + the CSV neighborhood, then
re-query LocationIQ. If the new pin is meaningfully different and still within
the city, update restaurants.json.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv

ROOT = Path(__file__).parent
RESTAURANTS = ROOT / "restaurants.json"
ZOMATO = ROOT / "zomato_data.json"

LOCATIONIQ_ENDPOINT = "https://us1.locationiq.com/v1/search"
RATE = 0.6
MIN_MOVE_KM = 0.4  # only update if new pin moved more than this
MAX_TRUSTED_MOVE_KM = 5.0   # moves bigger than this need an area-match
HARD_REJECT_MOVE_KM = 10.0  # moves bigger than this are always rejected

# Common Mumbai/Pune neighborhoods we'll cross-check against formatted_address
# when a re-geocode wants to move a pin a long way. If the source name mentions
# an area and the new address actually contains it, the move is trusted.
KNOWN_AREAS = [
    "Lower Parel", "Bandra", "Khar", "Worli", "Powai", "Andheri", "Juhu",
    "Colaba", "Fort", "Marine Lines", "Churchgate", "Tardeo", "Grant Road",
    "Lokhandwala", "Versova", "Goregaon", "Malad", "Borivali", "Vile Parle",
    "Santacruz", "Kalina", "BKC", "Bandra Kurla", "Kala Ghoda", "Marol",
    "Chembur", "Matunga", "Mahalaxmi", "Dadar", "Parel",
    "Kalyani Nagar", "Koregaon Park", "Viman Nagar", "Baner", "Aundh",
    "Kothrud", "Hinjewadi", "Hadapsar", "Mundhwa", "Kharadi",
]

CITY_BOUNDS = {
    "Mumbai":     (72.75, 18.85, 73.10, 19.35),
    "Pune":       (73.65, 18.35, 74.05, 18.70),
    "Delhi":      (76.80, 28.35, 77.40, 28.90),
    "Bangalore":  (77.40, 12.80, 77.85, 13.20),
    "Nashik":     (73.60, 19.85, 74.00, 20.15),
    "Jamshedpur": (86.05, 22.65, 86.35, 22.95),
}


def haversine_km(lat1, lng1, lat2, lng2):
    from math import radians, sin, cos, asin, sqrt
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng/2)**2
    return 2 * R * asin(sqrt(a))


def in_bounds(city, lat, lng):
    b = CITY_BOUNDS.get(city)
    if not b:
        return True
    return b[0] <= lng <= b[2] and b[1] <= lat <= b[3]


def locationiq_search(api_key, query, city, verify_ssl):
    params = {"key": api_key, "q": query, "format": "json", "limit": 1}
    bounds = CITY_BOUNDS.get(city)
    if bounds:
        lng_min, lat_min, lng_max, lat_max = bounds
        params["viewbox"] = f"{lng_min},{lat_max},{lng_max},{lat_min}"
        params["bounded"] = 1
    try:
        r = requests.get(LOCATIONIQ_ENDPOINT, params=params, timeout=20, verify=verify_ssl)
    except requests.RequestException as e:
        print(f"  ! network: {e}", file=sys.stderr)
        return None
    if r.status_code == 404 or r.status_code != 200:
        return None
    try:
        results = r.json()
    except ValueError:
        return None
    if not results:
        return None
    top = results[0]
    return float(top["lat"]), float(top["lon"]), top.get("display_name", "")


def build_query(csv_name, zomato_name, city):
    """Build the richest possible query.
    Prefer Zomato name (it's the canonical), but if CSV name has an area suffix
    and Zomato name doesn't, prepend the CSV area.
    """
    z = (zomato_name or "").strip()
    c = (csv_name or "").strip()
    has_z_area = "," in z
    has_c_area = "," in c
    if z and has_z_area:
        return f"{z}, {city}, India"
    if z and has_c_area:
        # CSV has area, Zomato name doesn't — combine
        c_area = c.split(",", 1)[1].strip()
        return f"{z}, {c_area}, {city}, India"
    if z:
        return f"{z}, {city}, India"
    return f"{c}, {city}, India"


def main() -> int:
    load_dotenv(ROOT / ".env")
    api_key = os.environ.get("LOCATIONIQ_API_KEY")
    if not api_key:
        print("ERROR: LOCATIONIQ_API_KEY not set", file=sys.stderr)
        return 1
    verify_ssl = os.environ.get("VERIFY_SSL", "0") == "1"
    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    restaurants = json.loads(RESTAURANTS.read_text())
    zomato_arr = json.loads(ZOMATO.read_text())
    zomato_by_key = {z["csv_key"].lower(): z for z in zomato_arr if z.get("matched")}

    # User-confirmed picks from manual_coords.json are sacred — never overwrite.
    manual_path = ROOT / "manual_coords.json"
    confirmed_keys: set[str] = set()
    if manual_path.exists():
        raw = json.loads(manual_path.read_text())
        overrides = raw.get("overrides", {}) if isinstance(raw, dict) else {}
        for k, v in overrides.items():
            if isinstance(v, dict) and v.get("user_confirmed") and v.get("lat") is not None:
                confirmed_keys.add(k.lower())

    updated = 0
    skipped_no_change = 0
    skipped_out_of_bounds = 0
    skipped_no_result = 0
    skipped_big_jump = 0
    not_matched = 0

    skipped_user_confirmed = 0
    for r in restaurants:
        name = r["Restaurant Name"]
        city = r.get("City", "")
        zkey = f"{name}|{city}".lower()
        if zkey in confirmed_keys:
            skipped_user_confirmed += 1
            continue
        z = zomato_by_key.get(zkey)
        if not z:
            not_matched += 1
            continue

        query = build_query(name, z.get("name"), city)
        print(f"[{name}] → {query!r}")

        result = locationiq_search(api_key, query, city, verify_ssl)
        time.sleep(RATE)

        if not result:
            skipped_no_result += 1
            continue

        new_lat, new_lng, new_addr = result
        if not in_bounds(city, new_lat, new_lng):
            skipped_out_of_bounds += 1
            continue

        old_lat, old_lng = r["lat"], r["lng"]
        dist = haversine_km(old_lat, old_lng, new_lat, new_lng)
        if dist < MIN_MOVE_KM:
            skipped_no_change += 1
            continue

        # Hard ceiling — anything beyond this is almost certainly wrong.
        if dist > HARD_REJECT_MOVE_KM:
            print(f"  rejected {dist:.2f} km jump (> {HARD_REJECT_MOVE_KM} km hard ceiling)")
            skipped_big_jump += 1
            continue

        # For mid-range jumps, require that the source name's area also appears
        # in the new formatted address — otherwise the geocoder probably wandered.
        if dist > MAX_TRUSTED_MOVE_KM:
            source_blob = f"{name} {z.get('name','')}".lower()
            mentioned = [a for a in KNOWN_AREAS if a.lower() in source_blob]
            new_addr_lower = (new_addr or "").lower()
            if mentioned and not any(a.lower() in new_addr_lower for a in mentioned):
                print(f"  rejected {dist:.2f} km jump (areas {mentioned} not in new addr: {new_addr[:80]!r})")
                skipped_big_jump += 1
                continue

        print(f"  moved {dist:.2f} km: ({old_lat:.4f},{old_lng:.4f}) → ({new_lat:.4f},{new_lng:.4f})")
        r["lat"] = new_lat
        r["lng"] = new_lng
        r["formatted_address"] = new_addr
        r["regeocoded_from_zomato"] = True
        updated += 1

    RESTAURANTS.write_text(json.dumps(restaurants, indent=2, ensure_ascii=False))

    print()
    print(f"✓ {updated} pins updated")
    print(f"  {skipped_no_change} kept (moved < {MIN_MOVE_KM} km)")
    print(f"  {skipped_big_jump} rejected (> {MAX_TRUSTED_MOVE_KM} km move with no area match)")
    print(f"  {skipped_no_result} no LocationIQ result")
    print(f"  {skipped_out_of_bounds} skipped (new pin outside city bounds)")
    print(f"  {skipped_user_confirmed} skipped (user-confirmed in manual_coords.json)")
    print(f"  {not_matched} venues without Zomato match (unchanged)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
