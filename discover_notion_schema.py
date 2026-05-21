#!/usr/bin/env python3
"""Print the Notion database schema so we can map properties correctly.

Run this once after creating your Notion integration and sharing the database
with it. Output goes to stdout — review the property names + types, then
adjust sync_notion.py's mapping if anything differs from what's expected.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv

ROOT = Path(__file__).parent
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

EXPECTED_PROPERTIES = {
    "Restaurant Name", "City", "Comment", "Cuisine Type",
    "Price", "Rating", "URL", "Visited",
}


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

    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
    }

    r = requests.get(f"{NOTION_API}/databases/{db_id}", headers=headers, timeout=20, verify=verify_ssl)
    if r.status_code != 200:
        print(f"ERROR: HTTP {r.status_code}", file=sys.stderr)
        print(r.text[:500], file=sys.stderr)
        if r.status_code == 404:
            print("\nHint: make sure you shared the database with your integration (DB → … → Connections).", file=sys.stderr)
        return 1

    data = r.json()
    title = "".join(t.get("plain_text", "") for t in (data.get("title") or []))
    props: dict = data.get("properties") or {}

    print(f"Database: {title or '(untitled)'}")
    print(f"ID: {db_id}")
    print(f"Properties: {len(props)}")
    print()
    print(f"{'Name':<28} {'Type':<16} {'Notes'}")
    print(f"{'-'*28} {'-'*16} {'-'*40}")

    found = set()
    for name, p in props.items():
        ptype = p.get("type", "?")
        notes = ""
        if ptype == "select":
            opts = (p.get("select") or {}).get("options", [])
            if opts:
                notes = f"options: {', '.join(o['name'] for o in opts[:5])}"
                if len(opts) > 5:
                    notes += f" (+{len(opts)-5} more)"
        elif ptype == "multi_select":
            opts = (p.get("multi_select") or {}).get("options", [])
            notes = f"{len(opts)} options"
        is_expected = "✓" if name in EXPECTED_PROPERTIES else " "
        print(f"{is_expected} {name:<26} {ptype:<16} {notes}")
        found.add(name)

    missing = EXPECTED_PROPERTIES - found
    if missing:
        print()
        print(f"⚠ Expected properties not found: {sorted(missing)}")
        print("  sync_notion.py's default mapping won't pick these up. Rename or update the mapping.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
