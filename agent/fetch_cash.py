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
                f"https://partners.fresha.com/reports/table/payment-summary?__pid={pid}",
                wait_until="networkidle",
            )
            await context.wait_for_timeout(3000)

            # Set custom date range to today
            try:
                await context.get_by_text("Month to date", exact=True).first.click(timeout=8000)
            except Exception:
                await context.get_by_text("Last week", exact=True).first.click(timeout=8000)
            await context.wait_for_timeout(500)
            await context.locator('select:has(option[value="custom"])').select_option(value="custom")
            await context.wait_for_timeout(500)

            # Fill date inputs
            date_inputs = context.locator('input[type="date"]')
            await date_inputs.nth(0).fill(date_str)
            await date_inputs.nth(1).fill(date_str)

            try:
                await context.get_by_role("button", name="Apply").click(timeout=5000)
            except Exception:
                pass
            await context.wait_for_load_state("networkidle")
            await context.wait_for_timeout(3000)

            # Apply location filter
            try:
                filter_btn = context.get_by_role("button", name="Filters")
                await filter_btn.click(timeout=8000)
                await context.wait_for_timeout(1000)

                loc_option = context.get_by_text(loc_name, exact=True)
                await loc_option.click(timeout=8000)
                await context.wait_for_timeout(500)

                try:
                    await context.get_by_role("button", name="Apply").click(timeout=5000)
                except Exception:
                    pass
                await context.wait_for_load_state("networkidle")
                await context.wait_for_timeout(3000)
            except Exception as e:
                print(f"    WARNING: Could not apply location filter: {e}")

            # Find Cash row in the table
            cash_value = 0.0
            try:
                rows = await context.locator("tr").all()
                for row in rows:
                    text = await row.inner_text()
                    if "Cash" in text and "$" in text:
                        # Extract the last dollar amount in the row
                        import re
                        amounts = re.findall(r'\$[\d,]+\.?\d*', text)
                        if amounts:
                            cash_value = float(amounts[-1].replace("$", "").replace(",", ""))
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

            await bctx.close()

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(run())
