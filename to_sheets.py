#!/usr/bin/env python3
"""Push restaurants.json into a Google Sheet so Google My Maps can read it.

Source of truth: restaurants.json (produced by sync_notion.py or geocode.py).
This script only transforms + writes; no Notion or LocationIQ calls.

Auth precedence:
  1. GOOGLE_SHEETS_CREDENTIALS env var (full JSON string) — used in CI.
  2. .cache/google-credentials.json file — local-dev fallback.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

ROOT = Path(__file__).parent
RESTAURANTS_PATH = ROOT / "restaurants.json"
ZOMATO_PATH = ROOT / "zomato_data.json"
LOCAL_CREDS_PATH = ROOT / ".cache" / "google-credentials.json"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER = [
    "Name",
    "Latitude",
    "Longitude",
    "Visited",
    "City",
    "Cuisine",
    "Price",
    "Rating",
    "Description",
    "URL",
    "Address",
    "Image",
]


def load_credentials() -> Credentials:
    raw = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if raw:
        info = json.loads(raw)
        return Credentials.from_service_account_info(info, scopes=SCOPES)
    if LOCAL_CREDS_PATH.exists():
        return Credentials.from_service_account_file(str(LOCAL_CREDS_PATH), scopes=SCOPES)
    raise RuntimeError(
        "No Google credentials found. Set GOOGLE_SHEETS_CREDENTIALS env var "
        f"(JSON string) or place the service-account JSON at {LOCAL_CREDS_PATH}."
    )


def load_zomato_lookup() -> dict[str, dict]:
    """Index zomato_data.json by lowercased 'name|city' — matches app.js:146."""
    if not ZOMATO_PATH.exists():
        return {}
    try:
        data = json.loads(ZOMATO_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    lookup: dict[str, dict] = {}
    for entry in data:
        if entry.get("matched") and entry.get("csv_key"):
            lookup[entry["csv_key"].lower()] = entry
    return lookup


def build_description(row: dict, z: dict | None) -> str:
    parts: list[str] = []
    comment = (row.get("Comment") or "").strip()
    if comment:
        parts.append(f"Notes: {comment}")

    if z:
        zbits: list[str] = []
        if z.get("rating"):
            zbits.append(f"⭐ {z['rating']}")
        if z.get("votes"):
            try:
                zbits.append(f"{int(z['votes']):,} votes")
            except (TypeError, ValueError):
                zbits.append(f"{z['votes']} votes")
        if z.get("eta") and z.get("serviceable"):
            zbits.append(f"🛵 {z['eta']}")
        if zbits:
            parts.append("Zomato: " + " · ".join(zbits))

    return "\n\n".join(parts)


def row_to_sheet_row(row: dict, zomato_lookup: dict[str, dict]) -> list:
    name = row.get("Restaurant Name", "")
    city = row.get("City", "")
    z = zomato_lookup.get(f"{name}|{city}".lower())
    return [
        name,
        row.get("lat", ""),
        row.get("lng", ""),
        row.get("Visited", ""),
        city,
        row.get("Cuisine Type", ""),
        row.get("Price", ""),
        row.get("Rating", ""),
        build_description(row, z),
        row.get("URL", ""),
        row.get("formatted_address", ""),
        (z or {}).get("image", "") or "",
    ]


def main() -> int:
    load_dotenv(ROOT / ".env")

    sheet_id = os.environ.get("GOOGLE_SHEETS_ID")
    if not sheet_id:
        print("ERROR: set GOOGLE_SHEETS_ID in .env or env (sheet ID from URL)", file=sys.stderr)
        return 1

    if not RESTAURANTS_PATH.exists():
        print(
            f"ERROR: {RESTAURANTS_PATH.name} not found — run sync_notion.py first",
            file=sys.stderr,
        )
        return 1

    restaurants = json.loads(RESTAURANTS_PATH.read_text(encoding="utf-8"))
    zomato_lookup = load_zomato_lookup()

    values = [HEADER] + [row_to_sheet_row(r, zomato_lookup) for r in restaurants]

    try:
        creds = load_credentials()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    sheets = service.spreadsheets()

    sheets.values().clear(spreadsheetId=sheet_id, range="A:Z").execute()
    sheets.values().update(
        spreadsheetId=sheet_id,
        range="A1",
        valueInputOption="RAW",
        body={"values": values},
    ).execute()

    print(f"Wrote {len(restaurants)} rows → Sheet {sheet_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
