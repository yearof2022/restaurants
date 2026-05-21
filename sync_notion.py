#!/usr/bin/env python3
"""Pull restaurants from a Notion database and run the geocode pipeline.

This replaces the CSV-driven flow: instead of editing the CSV and running
geocode.py, you add rows in Notion and run this. The cache in .cache/ means
repeated runs only hit LocationIQ for rows that are new since last sync.

Setup is in README.md (Notion integration token + database connection).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv

from geocode import geocode_rows

ROOT = Path(__file__).parent
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Property names in the Notion database. Adjust here if you renamed them.
# Run discover_notion_schema.py once to confirm these match.
PROP_NAME = "Restaurant Name"
PROP_CITY = "City"
PROP_COMMENT = "Comment"
PROP_CUISINE = "Cuisine Type"
PROP_PRICE = "Price"
PROP_RATING = "Rating"
PROP_URL = "URL"
PROP_VISITED = "Visited"


def extract_text(prop: dict | None) -> str:
    """Pull a string out of any Notion property, regardless of type."""
    if not prop:
        return ""
    ptype = prop.get("type")
    if ptype == "title":
        return "".join(t.get("plain_text", "") for t in (prop.get("title") or [])).strip()
    if ptype == "rich_text":
        return "".join(t.get("plain_text", "") for t in (prop.get("rich_text") or [])).strip()
    if ptype == "select":
        sel = prop.get("select")
        return (sel or {}).get("name", "") or ""
    if ptype == "multi_select":
        items = prop.get("multi_select") or []
        return ", ".join(i.get("name", "") for i in items if i.get("name"))
    if ptype == "url":
        return prop.get("url") or ""
    if ptype == "checkbox":
        return "Yes" if prop.get("checkbox") else "No"
    if ptype == "number":
        n = prop.get("number")
        return "" if n is None else str(n)
    if ptype == "status":
        s = prop.get("status")
        return (s or {}).get("name", "") or ""
    if ptype == "date":
        d = prop.get("date") or {}
        return d.get("start", "") or ""
    if ptype == "email":
        return prop.get("email") or ""
    if ptype == "phone_number":
        return prop.get("phone_number") or ""
    return ""


def page_to_row(page: dict) -> dict:
    """Map a Notion page object to the CSV-shaped dict that geocode_rows expects."""
    props = page.get("properties") or {}
    return {
        "Restaurant Name": extract_text(props.get(PROP_NAME)),
        "City":            extract_text(props.get(PROP_CITY)),
        "Comment":         extract_text(props.get(PROP_COMMENT)),
        "Cuisine Type":    extract_text(props.get(PROP_CUISINE)),
        "Price":           extract_text(props.get(PROP_PRICE)),
        "Rating":          extract_text(props.get(PROP_RATING)),
        "URL":             extract_text(props.get(PROP_URL)),
        "Visited":         extract_text(props.get(PROP_VISITED)),
    }


def fetch_all_pages(token: str, db_id: str, verify_ssl: bool) -> list[dict]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    url = f"{NOTION_API}/databases/{db_id}/query"
    all_pages: list[dict] = []
    cursor: str | None = None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(url, headers=headers, json=body, timeout=30, verify=verify_ssl)
        if r.status_code != 200:
            raise RuntimeError(f"Notion query failed: HTTP {r.status_code} — {r.text[:300]}")
        data = r.json()
        all_pages.extend(data.get("results") or [])
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        time.sleep(0.2)  # be polite — Notion allows 3 req/sec
    return all_pages


def main() -> int:
    load_dotenv(ROOT / ".env")
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DATABASE_ID")
    if not token or not db_id:
        print("ERROR: set NOTION_TOKEN and NOTION_DATABASE_ID in .env (see .env.example)", file=sys.stderr)
        return 1

    verify_ssl = os.environ.get("VERIFY_SSL", "0") == "1"
    if not verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    debug = "--debug" in sys.argv

    print(f"Fetching pages from Notion DB {db_id}…")
    try:
        pages = fetch_all_pages(token, db_id, verify_ssl)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    rows = [page_to_row(p) for p in pages]
    print(f"Fetched {len(rows)} rows from Notion.\n")

    if debug:
        print("First 5 rows after normalization:")
        for r in rows[:5]:
            print(" ", r)
        print()

    try:
        geocode_rows(rows)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
