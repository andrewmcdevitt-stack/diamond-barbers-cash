"""
fetch_cash.py
-------------
Opens Fresha Payments Summary for today, filters by each location,
reads the Cash row total, pushes to GHL custom values, and saves
reconciliation data to data/reconciliation/YYYY-MM-DD.json.

Run with:  python agent/fetch_cash.py [--group regular|night_markets]
Requires:  data/session.json        (NT Fresha session)
           data/session_cairns.json (QLD Fresha session)
"""

import argparse
import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

DATA_DIR  = Path(__file__).parent.parent / "data"
RECON_DIR = DATA_DIR / "reconciliation"
RECON_DIR.mkdir(parents=True, exist_ok=True)

GHL_API_KEY     = os.environ["GHL_API_KEY"]
GHL_LOCATION_ID = os.environ["GHL_LOCATION_ID"]
GHL_BASE        = "https://services.leadconnectorhq.com"
GHL_HEADERS     = {
    "Authorization": f"Bearer {GHL_API_KEY}",
    "Version":       "2021-07-28",
    "Accept":        "application/json",
    "Content-Type":  "application/json",
}

FORM_ID          = "3NbAwhSXgRjJFJNX0diN"
DARWIN_TZ        = timezone(timedelta(hours=9, minutes=30))
CAIRNS_TZ        = timezone(timedelta(hours=10))

TILL_FLOAT = 200.0  # standard float left in till after reset

# GHL form field IDs (confirmed via debug runs 2026-07-30)
FIELD_TILL_TOTAL  = "GE1Ur4QbYIAFah2fYiMx"   # till total barber counted
FIELD_RESET_TILL  = "cpiH5sh78VOyzKjilAzp"   # "Did you reset to $200?" → ['Yes'] or ['No']
FIELD_SUBLOC_NT   = "fgtUCQU1XqnQIEO02bwd"   # NT sub-location (e.g. "CBD", "Casuarina")
FIELD_SUBLOC_QLD  = "sGwpCs0TJqNGMkpDQ1BC"   # QLD sub-location (e.g. "Night Markets", "Showgrounds")

ACCOUNTS = [
    {
        "label":       "NT (Darwin + Parap)",
        "session":     DATA_DIR / "session.json",
        "provider_id": "1371504",
        "timezone":    DARWIN_TZ,
        "locations": [
            {"name": "Diamond Barbers - COOLALINGA",    "group": "regular"},
            {"name": "Diamond Barbers - BELLAMACK",     "group": "regular"},
            {"name": "Diamond Barbers - YARRAWONGA",    "group": "regular"},
            {"name": "Diamond Barbers - CASUARINA",     "group": "regular"},
            {"name": "Diamond Barbers - DARWIN CBD",    "group": "regular"},
            {"name": "Diamond Barbers - PARAP",         "group": "regular"},
            {"name": "Diamond Barbers - DELUXE",        "group": "regular"},
        ],
    },
    {
        "label":       "QLD (Cairns)",
        "session":     DATA_DIR / "session_cairns.json",
        "provider_id": "1390965",
        "timezone":    CAIRNS_TZ,
        "locations": [
            {"name": "Diamond Barbers Rising Sun",       "group": "regular"},
            {"name": "Diamond Barbers Showgrounds",      "group": "regular"},
            {"name": "Diamond Barbers Northern Beaches", "group": "regular"},
            {"name": "Diamond Barbers Night Markets",    "group": "night_markets"},
            {"name": "Diamond Barbers Wulguru",          "group": "regular"},
        ],
    },
]

ALL_LOCATION_NAMES = {
    loc["name"]
    for acct in ACCOUNTS
    for loc in acct["locations"]
}

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


# ── Reconciliation JSON ───────────────────────────────────────────────────────

def recon_path(date_str):
    return RECON_DIR / f"{date_str}.json"


def load_recon(date_str):
    p = recon_path(date_str)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"date": date_str, "locations": {}}


def save_recon(date_str, data):
    recon_path(date_str).write_text(json.dumps(data, indent=2))


# ── GHL helpers ───────────────────────────────────────────────────────────────

def ghl_set_custom_value(key, value):
    r = requests.get(
        f"{GHL_BASE}/locations/{GHL_LOCATION_ID}/customValues",
        headers=GHL_HEADERS,
    )
    if r.status_code not in (200, 201):
        raise Exception(f"Custom values fetch failed {r.status_code}: {r.text[:200]}")
    existing = {cv["name"]: cv["id"] for cv in r.json().get("customValues", [])}
    if key in existing:
        r = requests.put(
            f"{GHL_BASE}/locations/{GHL_LOCATION_ID}/customValues/{existing[key]}",
            headers=GHL_HEADERS,
            json={"name": key, "value": str(value)},
        )
    else:
        r = requests.post(
            f"{GHL_BASE}/locations/{GHL_LOCATION_ID}/customValues",
            headers=GHL_HEADERS,
            json={"name": key, "value": str(value)},
        )
    if r.status_code not in (200, 201):
        raise Exception(f"Custom value set failed {r.status_code}: {r.text[:200]}")


def ghl_update_cash(location_name, cash_sales):
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


def ghl_get_form_submissions(date_str):
    """Read all Cash Reconciliation form submissions for a given Darwin-date."""
    # Window: midnight to midnight Darwin time on date_str
    day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(
        tzinfo=DARWIN_TZ, hour=0, minute=0, second=0, microsecond=0
    )
    day_end  = day_start + timedelta(days=1)
    start_iso = day_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_iso   = day_end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    submissions = []
    page = 1
    while True:
        r = requests.get(
            f"{GHL_BASE}/forms/submissions",
            headers=GHL_HEADERS,
            params={
                "locationId": GHL_LOCATION_ID,
                "formId":     FORM_ID,
                "startAt":    start_iso,
                "endAt":      end_iso,
                "page":       page,
                "limit":      100,
            },
        )
        if r.status_code not in (200, 201):
            print(f"  WARNING: Form submissions fetch failed {r.status_code}: {r.text[:200]}")
            break
        data  = r.json()
        batch = data.get("submissions", [])
        submissions.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    print(f"  Found {len(submissions)} form submission(s) for {date_str}")
    return submissions


def parse_submission(sub):
    """Extract (location, till_total, reset_done, submitted_at) from a GHL form submission."""
    others       = sub.get("others", {})
    location     = None
    till_total   = None
    reset_done   = True   # default: assume reset if field missing (old submissions)
    submitted_at = sub.get("createdAt")

    # Till total
    raw_cash = others.get(FIELD_TILL_TOTAL)
    if raw_cash is not None:
        try:
            till_total = float(re.sub(r"[^\d.]", "", str(raw_cash)))
        except (ValueError, TypeError):
            pass

    # Reset field — returns a list e.g. ['Yes'] or ['No']
    reset_raw = others.get(FIELD_RESET_TILL)
    if isinstance(reset_raw, list) and reset_raw:
        reset_done = reset_raw[0].strip().lower() == "yes"
    elif isinstance(reset_raw, str):
        reset_done = reset_raw.strip().lower() == "yes"

    # Location: check both NT and QLD sub-location fields, match as substring
    subloc = (
        str(others.get(FIELD_SUBLOC_NT, "")).strip()
        or str(others.get(FIELD_SUBLOC_QLD, "")).strip()
    )
    if subloc:
        subloc_lower = subloc.lower()
        for loc_name in ALL_LOCATION_NAMES:
            if subloc_lower in loc_name.lower():
                location = loc_name
                break

    return location, till_total, reset_done, submitted_at


def get_prev_ending_till(date_str):
    """Return {loc_name: ending_till} from the previous day's recon file."""
    prev_date  = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_recon = load_recon(prev_date)
    return {
        loc: entry.get("ending_till", TILL_FLOAT)
        for loc, entry in prev_recon.get("locations", {}).items()
    }


# ── Fresha cash fetch ─────────────────────────────────────────────────────────

async def fetch_cash_for_account(account, page, date_str, group_filter):
    label     = account["label"]
    pid       = account["provider_id"]
    locations = [loc["name"] for loc in account["locations"] if loc["group"] == group_filter]

    if not locations:
        return {}

    print(f"\n{'='*60}")
    print(f"ACCOUNT: {label}  —  date: {date_str}  —  group: {group_filter}")
    print(f"{'='*60}")

    results = {}

    for loc_name in locations:
        print(f"\n  -- {loc_name} --")
        success = False
        for attempt in range(1, 4):
            if attempt > 1:
                print(f"    Retry {attempt}/3...")
                await page.wait_for_timeout(3000)
            try:
                await page.goto(
                    f"https://partners.fresha.com/reports/table/payments-summary?__pid={pid}",
                    wait_until="networkidle",
                )
                await page.wait_for_timeout(3000)

                await page.get_by_text("Month to date", exact=True).first.click(timeout=10000)
                await page.wait_for_timeout(1000)
                await page.locator('select:has(option[value="today"])').select_option(value="today")
                await page.wait_for_timeout(1000)
                try:
                    await page.get_by_role("button", name="Apply").click(timeout=5000)
                except Exception:
                    pass
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(10000)
                confirmed_url = page.url
                await page.goto(confirmed_url, wait_until="networkidle")
                await page.wait_for_timeout(5000)

                try:
                    await page.locator('[data-qa="open-filters-button"]').click(timeout=8000)
                    await page.wait_for_timeout(1500)
                    await page.get_by_text(loc_name, exact=True).first.dispatch_event('click')
                    await page.wait_for_timeout(500)
                    try:
                        await page.locator('[data-qa="filter-options-modal-apply"]').click(timeout=2000)
                        await page.wait_for_timeout(500)
                    except Exception:
                        pass
                    await page.locator('[data-qa="insights-apply-filters"]').click(timeout=5000)
                    await page.wait_for_load_state("networkidle")
                    await page.wait_for_timeout(2000)
                except Exception as e:
                    print(f"    WARNING: Could not apply location filter: {e}")

                cash_value = 0.0
                try:
                    rows = await page.locator("tr").all()
                    for row in rows:
                        text = await row.inner_text()
                        if "Cash" in text and "$" in text:
                            amounts = re.findall(r'A?\$\s*[\d,]+\.?\d*', text)
                            if amounts:
                                cash_value = float(re.sub(r'[A$,\s]', '', amounts[-1]))
                                break
                except Exception as e:
                    print(f"    WARNING: Could not read cash row: {e}")

                print(f"    Cash: ${cash_value:.2f}")
                results[loc_name] = cash_value
                success = True
                break

            except Exception as e:
                print(f"    ERROR (attempt {attempt}/3): {e}")

        if not success:
            print(f"    FAILED after 3 attempts: {loc_name}")
            results[loc_name] = None

    return results


# ── Main ──────────────────────────────────────────────────────────────────────

def build_submission_map(submissions, date_str):
    """Parse submissions and return {loc: {till_total, reset_done, ending_till, safe_deposit, submitted_at}}."""
    prev_ending = get_prev_ending_till(date_str)
    raw_map = {}
    for sub in submissions:
        loc, till_total, reset_done, ts = parse_submission(sub)
        if loc and till_total is not None:
            if loc not in raw_map or (ts or "") > (raw_map[loc]["submitted_at"] or ""):
                raw_map[loc] = {"till_total": till_total, "reset_done": reset_done, "submitted_at": ts}
            print(f"  Submission: {loc} till=${till_total:.2f} reset={reset_done} at {ts}")
        else:
            print(f"  WARNING: Could not parse submission id={sub.get('id')} (loc={loc}, till={till_total})")

    result = {}
    for loc, d in raw_map.items():
        till    = d["till_total"]
        reset   = d["reset_done"]
        prev    = prev_ending.get(loc, TILL_FLOAT)
        safe    = round(till - prev, 2)
        ending  = TILL_FLOAT if reset else till
        result[loc] = {
            "till_total":   till,
            "reset_done":   reset,
            "ending_till":  ending,
            "safe_deposit": safe,
            "submitted_at": d["submitted_at"],
        }
    return result


def build_recon_entry(fresha_cash, sub_data):
    """Build a reconciliation dict for one location."""
    if sub_data is None:
        return {
            "fresha_expected": fresha_cash,
            "till_total":      None,
            "reset_done":      None,
            "ending_till":     TILL_FLOAT,  # assume standard float for tomorrow
            "safe_deposit":    None,
            "variance":        None,
            "submitted_at":    None,
            "status":          "not_submitted",
        }
    safe     = sub_data["safe_deposit"]
    variance = round(safe - fresha_cash, 2) if fresha_cash is not None else None
    status   = (
        "not_reset"  if not sub_data["reset_done"]
        else "match"    if variance == 0.0
        else "variance" if variance is not None
        else "not_submitted"
    )
    return {
        "fresha_expected": fresha_cash,
        "till_total":      sub_data["till_total"],
        "reset_done":      sub_data["reset_done"],
        "ending_till":     sub_data["ending_till"],
        "safe_deposit":    safe,
        "variance":        variance,
        "submitted_at":    sub_data["submitted_at"],
        "status":          status,
    }


async def run(group_filter):
    from playwright.async_api import async_playwright
    today_darwin = datetime.now(DARWIN_TZ).strftime("%Y-%m-%d")

    recon       = load_recon(today_darwin)
    submissions = ghl_get_form_submissions(today_darwin)
    sub_map     = build_submission_map(submissions, today_darwin)

    async with async_playwright() as p:
        for account in ACCOUNTS:
            session_file = account["session"]
            if not session_file.exists():
                print(f"WARNING: {session_file.name} not found — skipping {account['label']}.")
                continue

            tz    = account["timezone"]
            today = datetime.now(tz).strftime("%Y-%m-%d")

            bctx    = await p.chromium.launch(headless=True)
            context = await bctx.new_context(storage_state=str(session_file))
            page    = await context.new_page()

            cash_results = await fetch_cash_for_account(account, page, today, group_filter)

            print(f"\n  Pushing cash data to GHL...")
            for loc_name, fresha_cash in cash_results.items():
                if fresha_cash is None:
                    print(f"    SKIP  {loc_name}  (error reading cash)")
                    sub_data = sub_map.get(loc_name)
                    entry = build_recon_entry(None, sub_data)
                    entry["status"] = "fetch_error"
                    recon["locations"][loc_name] = entry
                    continue

                try:
                    result = ghl_update_cash(loc_name, fresha_cash)
                    print(f"    {'SKIP' if result == 'no_record' else 'OK  '}  {loc_name:45s}  cash=${fresha_cash:.2f}")
                except Exception as e:
                    print(f"    ERROR {loc_name}: {e}")

                cv_key = LOCATION_CUSTOM_VALUE_KEY.get(loc_name)
                if cv_key:
                    try:
                        ghl_set_custom_value(cv_key, f"{fresha_cash:.2f}")
                        print(f"    CV    {cv_key}  = ${fresha_cash:.2f}")
                    except Exception as e:
                        print(f"    CV ERROR {loc_name}: {e}")

                recon["locations"][loc_name] = build_recon_entry(fresha_cash, sub_map.get(loc_name))

            await bctx.close()

    # Record any locations in this group not yet reached (session missing etc.)
    group_locs = {
        loc["name"]
        for acct in ACCOUNTS
        for loc in acct["locations"]
        if loc["group"] == group_filter
    }
    for loc_name in group_locs:
        if loc_name not in recon["locations"]:
            entry = build_recon_entry(None, sub_map.get(loc_name))
            entry["status"] = "fetch_error"
            recon["locations"][loc_name] = entry

    save_recon(today_darwin, recon)
    print(f"\nReconciliation saved: {recon_path(today_darwin)}")
    print("Done.")


def reprocess(date_str):
    """Re-read GHL submissions for a past date and patch the existing recon file."""
    recon = load_recon(date_str)
    if not recon.get("locations"):
        print(f"No recon file found for {date_str} — nothing to patch.")
        return

    print(f"Re-processing submissions for {date_str}...")
    submissions = ghl_get_form_submissions(date_str)
    sub_map     = build_submission_map(submissions, date_str)

    for loc_name, entry in recon["locations"].items():
        sub_data = sub_map.get(loc_name)
        if sub_data:
            fresha = entry.get("fresha_expected")
            new    = build_recon_entry(fresha, sub_data)
            entry.update(new)
            print(f"  Updated {loc_name}: safe=${sub_data['safe_deposit']:.2f} reset={sub_data['reset_done']} variance={new['variance']} status={new['status']}")

    save_recon(date_str, recon)
    print(f"Recon file updated: {recon_path(date_str)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--group",
        choices=["regular", "night_markets"],
        default="regular",
        help="Which group of locations to process",
    )
    parser.add_argument(
        "--reprocess",
        metavar="YYYY-MM-DD",
        help="Re-read GHL submissions for a past date and patch its recon file (no Fresha scrape).",
    )
    args = parser.parse_args()
    if args.reprocess:
        reprocess(args.reprocess)
    else:
        asyncio.run(run(args.group))
