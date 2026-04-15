"""
fetch_cash.py
-------------
Opens Fresha Payments Summary for today, filters by each location,
reads the Cash row total, and pushes it to the GHL location_performance
custom object as cash_sales.

Run with:  python agent/fetch_cash.py
Requires:  data/session.json        (NT Fresha session)
           data/session_cairns.json (QLD Fresha session)
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

DATA_DIR = Path(__file__).parent.parent / "data"

GHL_API_KEY     = os.environ["GHL_API_KEY"]
GHL_LOCATION_ID = os.environ["GHL_LOCATION_ID"]
GHL_BASE        = "https://services.leadconnectorhq.com"
GHL_HEADERS     = {
    "Authorization": f"Bearer {GHL_API_KEY}",
    "Version":       "2021-07-28",
    "Accept":        "application/json",
    "Content-Type":  "application/json",
}

# All locations to fetch cash for, grouped by Fresha account
ACCOUNTS = [
    {
        "label":       "NT (Darwin + Parap)",
        "session":     DATA_DIR / "session.json",
        "provider_id": "1371504",
        "timezone":    timezone(timedelta(hours=9, minutes=30)),
        "locations": [
            "Diamond Barbers - COOLALINGA",
            "Diamond Barbers - BELLAMACK",
            "Diamond Barbers - YARRAWONGA",
            "Diamond Barbers - CASUARINA",
            "Diamond Barbers - DARWIN CBD",
            "Diamond Barbers - PARAP",
            "Diamond Barbers - DELUXE",
        ],
    },
    {
        "label":       "QLD (Cairns)",
        "session":     DATA_DIR / "session_cairns.json",
        "provider_id": "1390965",
        "timezone":    timezone(timedelta(hours=10)),
        "locations": [
            "Diamond Barbers Rising Sun",
            "Diamond Barbers Showgrounds",
            "Diamond Barbers Northern Beaches",
            "Diamond Barbers Night Markets",
            "Diamond Barbers Wulguru",
        ],
    },
]


# ── Location name → custom value key ─────────────────────────────────────────

LOCATION_CUSTOM_VALUE_KEY = {
    "Diamond Barbers - COOLALINGA":    "fresha_cash_darwin_coolalinga",
    "Diamond Barbers - BELLAMACK":     "fresha_cash_darwin_bellamack",
    "Diamond Barbers - YARRAWONGA":    "fresha_cash_darwin_yarrawonga",
    "Diamond Barbers - CASUARINA":     "fresha_cash_darwin_casuarina",
    "Diamond Barbers - DARWIN CBD":    "fresha_cash_darwin_cbd",
    "Diamond Barbers - PARAP":         "fresha_cash_darwin_parap",
    "Diamond Barbers - DELUXE":        "fresha_cash_darwin_deluxe",
    "Diamond Barbers Showgrounds":     "fresha_cash_cairns_showgrounds",
    "Diamond Barbers Northern Beaches":"fresha_cash_cairns_northern_beaches",
    "Diamond Barbers Night Markets":   "fresha_cash_cairns_night_markets",
    "Diamond Barbers Wulguru":         "fresha_cash_townsville_wulguru",
    "Diamond Barbers Rising Sun":      "fresha_cash_townsville_rising_sun",
}


def ghl_set_custom_value(key, value):
    """Create or update a GHL location custom value by key name."""
    # Search for existing custom value
    r = requests.get(
        f"{GHL_BASE}/locations/{GHL_LOCATION_ID}/customValues",
        headers=GHL_HEADERS,
    )
    if r.status_code not in (200, 201):
        raise Exception(f"Custom values fetch failed {r.status_code}: {r.text[:200]}")

    existing = {cv["name"]: cv["id"] for cv in r.json().get("customValues", [])}

    if key in existing:
        # Update
        r = requests.put(
            f"{GHL_BASE}/locations/{GHL_LOCATION_ID}/customValues/{existing[key]}",
            headers=GHL_HEADERS,
            json={"name": key, "value": str(value)},
        )
    else:
        # Create
        r = requests.post(
            f"{GHL_BASE}/locations/{GHL_LOCATION_ID}/customValues",
            headers=GHL_HEADERS,
            json={"name": key, "value": str(value)},
        )

    if r.status_code not in (200, 201):
        raise Exception(f"Custom value set failed {r.status_code}: {r.text[:200]}")


# ── GHL helper ────────────────────────────────────────────────────────────────

def ghl_update_cash(location_name, cash_sales):
    """Find the most recent location_performance record and update cash_sales."""
    r = requests.post(
        f"{GHL_BASE}/objects/custom_objects.location_performance/records/search",
        headers=GHL_HEADERS,
        json={
            "locationId": GHL_LOCATION_ID,
            "page":        1,
            "pageLimit":   10,
            "filters": [
                {"field": "properties.location_name", "operator": "eq", "value": location_name},
            ],
        },
    )
    if r.status_code not in (200, 201):
        raise Exception(f"Search failed {r.status_code}: {r.text[:200]}")

    records = r.json().get("records", [])
    if not records:
        return "no_record"

    # Use the most recent record
    record_id = records[0]["id"]
    r = requests.put(
        f"{GHL_BASE}/objects/custom_objects.location_performance/records/{record_id}",
        headers=GHL_HEADERS,
        params={"locationId": GHL_LOCATION_ID},
        json={"properties": {"cash_sales": cash_sales}},
    )
    if r.status_code in (200, 201):
        return "updated"
    raise Exception(f"GHL {r.status_code}: {r.text[:200]}")


# ── Fresha cash fetch ─────────────────────────────────────────────────────────

async def fetch_cash_for_account(account, context, date_str):
    label     = account["label"]
    pid       = account["provider_id"]
    locations = account["locations"]

    print(f"\n{'='*60}")
    print(f"ACCOUNT: {label}  —  date: {date_str}")
    print(f"{'='*60}")

    results = {}

    for loc_name in locations:
        print(f"\n  -- {loc_name} --")
        try:
            # Navigate to Payments Summary
            await context.goto(
                f"https://partners.fresha.com/reports/table/payments-summary?__pid={pid}",
                wait_until="networkidle",
            )
            await context.wait_for_timeout(3000)

            # Set date to Today — copied from fetch_performance.py pattern
            await context.get_by_text("Month to date", exact=True).first.click(timeout=10000)
            await context.wait_for_timeout(1000)
            await context.locator('select:has(option[value="today"])').select_option(value="today")
            await context.wait_for_timeout(1000)
            try:
                await context.get_by_role("button", name="Apply").click(timeout=5000)
            except Exception:
                pass
            await context.wait_for_load_state("networkidle")
            await context.wait_for_timeout(10000)
            # Reload the confirmed URL (same as fetch_performance.py)
            confirmed_url = context.url
            await context.goto(confirmed_url, wait_until="networkidle")
            await context.wait_for_timeout(5000)

            # Apply location filter using data-qa attribute
            try:
                await context.locator('[data-qa="open-filters-button"]').click(timeout=8000)
                await context.wait_for_timeout(1000)
                await context.get_by_text(loc_name, exact=True).first.dispatch_event('click')
                await context.wait_for_timeout(500)
                # Close the team member modal if open, then click main Apply
                try:
                    await context.locator('[data-qa="filter-options-modal-apply"]').click(timeout=2000)
                    await context.wait_for_timeout(500)
                except Exception:
                    pass
                await context.locator('[data-qa="insights-apply-filters"]').click(timeout=5000)
                await context.wait_for_load_state("networkidle")
                await context.wait_for_timeout(2000)
            except Exception as e:
                print(f"    WARNING: Could not apply location filter: {e}")

            # Find Cash row in the table
            cash_value = 0.0
            try:
                rows = await context.locator("tr").all()
                for row in rows:
                    text = await row.inner_text()
                    if "Cash" in text and "$" in text:
                        import re
                        # Match A$ 201.38 or $201.38 or A$201.38
                        amounts = re.findall(r'A?\$\s*[\d,]+\.?\d*', text)
                        if amounts:
                            cash_value = float(re.sub(r'[A$,\s]', '', amounts[-1]))
                            break
            except Exception as e:
                print(f"    WARNING: Could not read cash row: {e}")

            print(f"    Cash: ${cash_value:.2f}")
            results[loc_name] = cash_value

        except Exception as e:
            print(f"    ERROR: {e}")
            results[loc_name] = None

    return results


# ── Main ──────────────────────────────────────────────────────────────────────

async def run():
    async with async_playwright() as p:
        for account in ACCOUNTS:
            session_file = account["session"]
            if not session_file.exists():
                print(f"WARNING: {session_file.name} not found — skipping {account['label']}.")
                continue

            tz       = account["timezone"]
            today    = datetime.now(tz).strftime("%Y-%m-%d")

            bctx    = await p.chromium.launch(headless=True)
            context = await bctx.new_context(storage_state=str(session_file))
            page    = await context.new_page()

            cash_results = await fetch_cash_for_account(account, page, today)

            print(f"\n  Pushing cash data to GHL...")
            for loc_name, cash in cash_results.items():
                if cash is None:
                    print(f"    SKIP  {loc_name}  (error reading cash)")
                    continue
                try:
                    result = ghl_update_cash(loc_name, cash)
                    if result == "no_record":
                        print(f"    SKIP  {loc_name}  (no GHL record)")
                    else:
                        print(f"    OK    {loc_name:45s}  cash=${cash:.2f}")
                except Exception as e:
                    print(f"    ERROR {loc_name}: {e}")

                # Also push as a location custom value for use in notifications
                cv_key = LOCATION_CUSTOM_VALUE_KEY.get(loc_name)
                if cv_key:
                    try:
                        ghl_set_custom_value(cv_key, f"{cash:.2f}")
                        print(f"    CV    {cv_key}  = ${cash:.2f}")
                    except Exception as e:
                        print(f"    CV ERROR {loc_name}: {e}")

            await bctx.close()

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(run())
