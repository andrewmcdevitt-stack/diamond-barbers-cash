"""
send_cash_report.py
-------------------
Reads yesterday's cash reconciliation JSON and sends a summary email
to claude@diamondbarbers.com.au.

Run with:  python agent/send_cash_report.py
Scheduled: 21:30 UTC daily (7:00 AM Darwin next morning)
"""

import json
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATA_DIR  = Path(__file__).parent.parent / "data"
RECON_DIR = DATA_DIR / "reconciliation"

DARWIN_TZ  = timezone(timedelta(hours=9, minutes=30))
EMAIL_FROM = "claude@diamondbarbers.com.au"
EMAIL_TO   = "admin@diamondbarbers.com.au"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "mail.diamondbarbers.com.au")
EMAIL_PASS = os.environ.get("EMAIL_PASSWORD", "")

DISPLAY_ORDER = [
    "Diamond Barbers - DARWIN CBD",
    "Diamond Barbers - CASUARINA",
    "Diamond Barbers - COOLALINGA",
    "Diamond Barbers - BELLAMACK",
    "Diamond Barbers - YARRAWONGA",
    "Diamond Barbers - PARAP",
    "Diamond Barbers - DELUXE",
    "Diamond Barbers Rising Sun",
    "Diamond Barbers Showgrounds",
    "Diamond Barbers Northern Beaches",
    "Diamond Barbers Wulguru",
    "Diamond Barbers Night Markets",
]


def fmt_currency(v):
    if v is None:
        return "—"
    return f"${v:,.2f}"


def fmt_variance(v):
    if v is None:
        return "—"
    if v > 0:
        return f"+${v:,.2f}"
    if v < 0:
        return f"-${abs(v):,.2f}"
    return "$0.00"


def reset_cell(reset_done):
    s = "padding:8px 6px;text-align:center;width:50px;"
    if reset_done is True:
        return f'<td style="{s}"><span style="color:#16a34a;font-size:15px;">&#10003;</span></td>'
    if reset_done is False:
        return f'<td style="{s}"><span style="color:#dc2626;font-size:15px;">&#10007;</span></td>'
    return f'<td style="{s}"><span style="color:#d1d5db;font-size:15px;">&minus;</span></td>'


def status_cell(status):
    s = "padding:8px 12px 8px 6px;text-align:center;width:60px;"
    if status == "match":
        return f'<td style="{s}"><span style="color:#16a34a;font-weight:700;">Pass</span></td>'
    if status in ("variance", "not_reset"):
        return f'<td style="{s}"><span style="color:#dc2626;font-weight:700;">Fail</span></td>'
    return f'<td style="{s}"><span style="color:#d1d5db;font-weight:600;">&mdash;</span></td>'


def build_html(date_str, locations):
    try:
        display_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%-d %B %Y")
    except Exception:
        display_date = date_str

    matched   = sum(1 for d in locations.values() if d["status"] == "match")
    variances = sum(1 for d in locations.values() if d["status"] in ("variance", "not_reset"))
    missing   = sum(1 for d in locations.values() if d["status"] in ("not_submitted", "fetch_error"))
    has_issues = variances > 0 or missing > 0

    ordered = sorted(
        locations.items(),
        key=lambda x: DISPLAY_ORDER.index(x[0]) if x[0] in DISPLAY_ORDER else 99,
    )

    rows = ""
    for loc_name, d in ordered:
        status     = d.get("status", "fetch_error")
        expected   = d.get("fresha_expected")
        accumulated = d.get("accumulated_fresha", 0.0) or 0.0
        till_total = d.get("till_total")
        safe_dep   = d.get("safe_deposit") or d.get("counted")  # backward compat
        variance   = d.get("variance")
        reset_done = d.get("reset_done")

        # When days have been missed, show the running total rather than just today's Fresha
        if accumulated > (expected or 0) and status == "not_submitted":
            expected = accumulated

        var_color = ""
        if status in ("variance", "not_reset"):
            var_color = "color:#ef4444;"
        elif status == "match":
            var_color = "color:#22c55e;"

        rows += f"""
        <tr style="border-bottom:1px solid #e5e7eb;">
          <td style="padding:8px 8px 8px 12px;color:#111827;font-size:13px;">{loc_name}</td>
          <td style="padding:8px 6px;text-align:right;color:#6b7280;font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap;">{fmt_currency(expected)}</td>
          <td style="padding:8px 6px;text-align:right;color:#9ca3af;font-size:12px;font-variant-numeric:tabular-nums;white-space:nowrap;">{fmt_currency(till_total)}</td>
          <td style="padding:8px 6px;text-align:right;color:#111827;font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap;">{fmt_currency(safe_dep)}</td>
          <td style="padding:8px 6px;text-align:right;font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap;{var_color}">{fmt_variance(variance)}</td>
          {reset_cell(reset_done)}
          {status_cell(status)}
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:Inter,-apple-system,BlinkMacSystemFont,sans-serif;">
<div style="max-width:680px;margin:0 auto;padding:32px 16px;">

  <div style="margin-bottom:20px;">
    <div style="font-size:22px;font-weight:700;color:#111827;">Cash Reconciliation</div>
    <div style="font-size:13px;color:#6b7280;margin-top:4px;">Diamond Barbers &nbsp;·&nbsp; {display_date}</div>
  </div>

  <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;margin-bottom:24px;">
    <table style="width:auto;border-collapse:collapse;">
      <thead>
        <tr style="border-bottom:1px solid #e5e7eb;background:#f9fafb;">
          <th style="padding:8px 8px 8px 12px;text-align:left;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;font-weight:600;">Location</th>
          <th style="padding:8px 6px;text-align:right;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;font-weight:600;width:1px;white-space:nowrap;">Fresha</th>
          <th style="padding:8px 6px;text-align:right;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;font-weight:600;width:1px;white-space:nowrap;">Till</th>
          <th style="padding:8px 6px;text-align:right;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;font-weight:600;width:1px;white-space:nowrap;">Banked</th>
          <th style="padding:8px 6px;text-align:right;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;font-weight:600;width:1px;white-space:nowrap;">Difference</th>
          <th style="padding:8px 6px;text-align:center;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;font-weight:600;width:1px;white-space:nowrap;">Reset</th>
          <th style="padding:8px 12px 8px 6px;text-align:center;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.06em;font-weight:600;width:1px;white-space:nowrap;">Status</th>
        </tr>
      </thead>
      <tbody>{rows}
      </tbody>
    </table>
  </div>

  <div style="font-size:11px;color:#9ca3af;text-align:center;">
    Diamond Barbers &nbsp;·&nbsp; Daily Cash Reconciliation &nbsp;·&nbsp; {display_date}
  </div>

</div>
</body>
</html>""", has_issues


def send_report(date_str, locations):
    html, has_issues = build_html(date_str, locations)

    try:
        display_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%-d %b %Y")
    except Exception:
        display_date = date_str

    flag    = " ⚠" if has_issues else " ✓"
    subject = f"Cash Reconciliation{flag} — {display_date}"

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(EMAIL_HOST, 587) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(EMAIL_FROM, EMAIL_PASS)
            smtp.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print(f"  Report emailed to {EMAIL_TO}")
    except Exception as e:
        print(f"  ERROR: Email failed: {e}")


def run(date_str=None):
    if not date_str:
        today_darwin = datetime.now(DARWIN_TZ).date()
        date_str     = (today_darwin - timedelta(days=1)).strftime("%Y-%m-%d")

    recon_file = RECON_DIR / f"{date_str}.json"
    if not recon_file.exists():
        print(f"No reconciliation file found for {date_str} — nothing to send.")
        return

    data      = json.loads(recon_file.read_text())
    locations = data.get("locations", {})

    if not locations:
        print(f"No location data in {recon_file.name} — nothing to send.")
        return

    print(f"Sending reconciliation report for {date_str}...")
    for loc, d in locations.items():
        print(f"  {loc}: expected={d.get('fresha_expected')}, counted={d.get('counted')}, status={d.get('status')}")

    send_report(date_str, locations)
    print("Done.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Date to report on (YYYY-MM-DD). Defaults to yesterday.")
    args = parser.parse_args()
    run(args.date)
