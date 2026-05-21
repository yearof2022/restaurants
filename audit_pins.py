#!/usr/bin/env python3
"""Surface the most-likely-wrong pins from restaurants.json.

Writes audit.json with ~15-20 suspects sorted by suspicion, each tagged with the
reason(s) it was flagged. Used as input to the interactive pin-review flow.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
RESTAURANTS = ROOT / "restaurants.json"
ZOMATO = ROOT / "zomato_data.json"
MANUAL = ROOT / "manual_coords.json"
OUT = ROOT / "audit.json"

MAX_SUSPECTS = 20

# Restaurant chains where a bare name (no neighborhood in CSV) often pins ambiguously
CHAINS = {
    "social", "hard rock cafe", "toit", "effingut", "pop tate", "1441",
    "joey's pizza", "joeys pizza", "beer cafe", "mainland china",
    "brewdog", "third wave coffee", "starbucks", "blue tokai",
}

# Common Mumbai/Pune areas — used to check if the formatted_address actually
# contains the area the venue name claims it's in.
AREAS = [
    "Lower Parel", "Bandra", "Khar", "Worli", "Powai", "Andheri", "Juhu",
    "Colaba", "Fort", "Marine Lines", "Churchgate", "Tardeo", "Grant Road",
    "Lokhandwala", "Versova", "Goregaon", "Malad", "Borivali", "Vile Parle",
    "Santacruz", "Kalina", "BKC", "Bandra Kurla", "Kala Ghoda", "Marol",
    "Chembur", "Matunga", "Mahalaxmi", "Dadar", "Parel", "SoBo", "Seawoods",
    "Navi Mumbai", "Kamala Mills",
    "Kalyani Nagar", "Koregaon Park", "Viman Nagar", "Baner", "Aundh",
    "Kothrud", "Hinjewadi", "Hadapsar", "Mundhwa", "Kharadi",
]


def load_user_confirmed() -> set[str]:
    """Return the lowercased csv_keys that the user has already confirmed."""
    if not MANUAL.exists():
        return set()
    raw = json.loads(MANUAL.read_text())
    overrides = raw.get("overrides", {}) if isinstance(raw, dict) else {}
    confirmed = set()
    for key, val in overrides.items():
        if isinstance(val, dict) and val.get("user_confirmed") and val.get("lat") is not None:
            confirmed.add(key.lower())
    return confirmed


def find_fallback_clusters(rows: list[dict]) -> set[tuple[float, float]]:
    """LocationIQ tends to fall back to a few default coordinates. Find them by
    looking for lat/lng pairs that 5+ restaurants share (rounded to 4 decimals)."""
    rounded = Counter((round(r["lat"], 4), round(r["lng"], 4)) for r in rows)
    return {coord for coord, n in rounded.items() if n >= 5}


def is_generic_address(addr: str) -> bool:
    """Address like 'Mumbai, Maharashtra, India' with no street info."""
    if not addr:
        return True
    cleaned = addr.lower().strip()
    # Strip "Mumbai City District" / "Mumbai Zone X" — Nominatim noise
    cleaned = re.sub(r"mumbai zone \d+,?\s*", "", cleaned)
    cleaned = re.sub(r"mumbai city.*?,?\s*", "", cleaned)
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    # Generic if all parts are city/state/country level
    generic_words = {"mumbai", "pune", "delhi", "bangalore", "bengaluru",
                     "nashik", "jamshedpur", "maharashtra", "karnataka",
                     "india", "ncr", "new delhi"}
    return all(p in generic_words for p in parts)


def name_mentions_chain(name: str) -> str | None:
    nlow = name.lower()
    for chain in CHAINS:
        if chain in nlow:
            return chain
    return None


def name_implied_area(name: str) -> str | None:
    """If CSV name has a comma like 'Eve, Powai', return 'Powai'."""
    if "," not in name:
        return None
    tail = name.split(",", 1)[1].strip()
    for a in AREAS:
        if a.lower() in tail.lower():
            return a
    # If tail isn't a known area, still return it as a hint
    return tail if tail else None


def addr_contains_area(addr: str, area: str) -> bool:
    if not addr or not area:
        return False
    return area.lower() in addr.lower()


def main() -> int:
    restaurants = json.loads(RESTAURANTS.read_text())
    zomato = json.loads(ZOMATO.read_text()) if ZOMATO.exists() else []
    z_matched = {z["csv_key"].lower(): z for z in zomato if z.get("matched")}
    confirmed = load_user_confirmed()
    clusters = find_fallback_clusters(restaurants)

    suspects: list[dict] = []
    for r in restaurants:
        name = r.get("Restaurant Name", "")
        city = r.get("City", "")
        key = f"{name}|{city}".lower()

        if key in confirmed:
            continue
        if r.get("place_id") == "manual":
            continue

        reasons: list[str] = []
        score = 0
        addr = r.get("formatted_address", "")
        coord = (round(r.get("lat", 0), 4), round(r.get("lng", 0), 4))

        # 1. fallback cluster
        if coord in clusters:
            reasons.append(f"fallback_cluster({coord[0]},{coord[1]})")
            score += 3

        # 2. generic address
        if is_generic_address(addr):
            reasons.append("generic_address")
            score += 2

        # 3. chain without outlet
        chain = name_mentions_chain(name)
        if chain and "," not in name:
            reasons.append(f"chain_no_outlet({chain})")
            score += 2

        # 4. name claims an area that's not in the formatted_address
        implied = name_implied_area(name)
        if implied and not addr_contains_area(addr, implied):
            reasons.append(f"name_says_{implied!r}_addr_doesnt")
            score += 2

        # 5. Zomato said the venue is in a specific area; addr doesn't have that area
        z = z_matched.get(key)
        if z:
            z_name = z.get("name", "")
            if "," in z_name:
                z_area = z_name.split(",")[-1].strip()
                if z_area and not addr_contains_area(addr, z_area):
                    reasons.append(f"zomato_says_{z_area!r}_addr_doesnt")
                    score += 1

        # 6. No Zomato match AND fallback-coordinate-y AND generic addr → triple whammy
        if not z and coord in clusters and is_generic_address(addr):
            reasons.append("no_zomato+fallback+generic")
            score += 2  # already counted by 1+2; this just bumps score

        if score == 0:
            continue

        suspects.append({
            "csv_key": f"{name}|{city}",
            "name": name,
            "city": city,
            "current": {
                "lat": r.get("lat"),
                "lng": r.get("lng"),
                "address": addr,
            },
            "zomato": (
                {"name": z["name"], "rating": z.get("rating"), "votes": z.get("votes")}
                if z else None
            ),
            "score": score,
            "reasons": reasons,
        })

    suspects.sort(key=lambda x: -x["score"])
    suspects = suspects[:MAX_SUSPECTS]

    OUT.write_text(json.dumps(suspects, indent=2, ensure_ascii=False))

    print(f"Found {len(suspects)} suspect pins (top {MAX_SUSPECTS} by score).")
    print(f"Wrote → {OUT.name}\n")
    for s in suspects:
        z_info = f"  Z={s['zomato']['name']!r}" if s["zomato"] else "  (no Zomato)"
        print(f"  [{s['score']}] {s['name']!r} ({s['city']})  →  {s['current']['lat']:.4f},{s['current']['lng']:.4f}{z_info}")
        print(f"        reasons: {', '.join(s['reasons'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
