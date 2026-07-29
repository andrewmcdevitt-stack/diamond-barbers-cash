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
EMAIL_TO   = "claude@diamondbarbers.com.au"
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


def status_cell(status):
    if status == "match":
        return '<td style="padding:10px 12px;text-align:center;"><span style="color:#22c55e;font-weight:700;">&#10003; Match</span></td>'
    if status == "variance":
        return '<td style="padding:10px 12px;text-align:center;"><span style="color:#ef4444;font-weight:700;">&#9888; Variance</span></td>'
    if status == "not_submitted":
        return '<td style="padding:10px 12px;text-align:center;"><span style="color:#f59e0b;font-weight:700;">&#10007; Not submitted</span></td>'
    return '<td style="padding:10px 12px;text-align:center;"><span style="color:#888;font-weight:700;">&#10007; Fetch error</span></td>'


def build_html(date_str, locations):
    try:
        display_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%-d %B %Y")
    except Exception:
        display_date = date_str

    matched   = sum(1 for d in locations.values() if d["status"] == "match")
    variances = sum(1 for d in locations.values() if d["status"] == "variance")
    missing   = sum(1 for d in locations.values() if d["status"] in ("not_submitted", "fetch_error"))
    has_issues = variances > 0 or missing > 0

    ordered = sorted(
        locations.items(),
        key=lambda x: DISPLAY_ORDER.index(x[0]) if x[0] in DISPLAY_ORDER else 99,
    )

    rows = ""
    for loc_name, d in ordered:
        status   = d.get("status", "fetch_error")
        expected = d.get("fresha_expected")
        counted  = d.get("counted")
        variance = d.get("variance")

        var_color = ""
        if status == "variance":
            var_color = "color:#ef4444;"
        elif status == "match":
            var_color = "color:#22c55e;"

        rows += f"""
        <tr style="border-bottom:1px solid #2a2a2a;">
          <td style="padding:10px 12px;color:#ffffff;font-size:13px;">{loc_name}</td>
          <td style="padding:10px 12px;text-align:right;color:#888;font-size:13px;font-variant-numeric:tabular-nums;">{fmt_currency(expected)}</td>
          <td style="padding:10px 12px;text-align:right;color:#ffffff;font-size:13px;font-variant-numeric:tabular-nums;">{fmt_currency(counted)}</td>
          <td style="padding:10px 12px;text-align:right;font-size:13px;font-variant-numeric:tabular-nums;{var_color}">{fmt_variance(variance)}</td>
          {status_cell(status)}
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#000000;font-family:Inter,-apple-system,BlinkMacSystemFont,sans-serif;">
<div style="max-width:680px;margin:0 auto;padding:32px 16px;">

  <div style="margin-bottom:24px;">
    <div style="font-size:22px;font-weight:700;color:#ffffff;">Cash Reconciliation</div>
    <div style="font-size:13px;color:#888888;margin-top:4px;">Diamond Barbers &nbsp;·&nbsp; {display_date}</div>
  </div>

  <table style="width:100%;border-collapse:collapse;margin-bottom:24px;">
    <tr>
      <td style="padding:0 8px 0 0;width:33%;">
        <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:16px 20px;">
          <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Matched</div>
          <div style="font-size:26px;font-weight:700;color:#22c55e;">{matched}</div>
        </div>
      </td>
      <td style="padding:0 4px;width:33%;">
        <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:16px 20px;">
          <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Variances</div>
          <div style="font-size:26px;font-weight:700;color:{'#ef4444' if variances else '#888888'};">{variances}</div>
        </div>
      </td>
      <td style="padding:0 0 0 8px;width:33%;">
        <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:16px 20px;">
          <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px;">Not Submitted</div>
          <div style="font-size:26px;font-weight:700;color:{'#f59e0b' if missing else '#888888'};">{missing}</div>
        </div>
      </td>
    </tr>
  </table>

  <div style="background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;overflow:hidden;margin-bottom:24px;">
    <table style="width:100%;border-collapse:collapse;">
      <thead>
        <tr style="border-bottom:1px solid #333333;">
          <th style="padding:10px 12px;text-align:left;font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.06em;font-weight:600;">Location</th>
          <th style="padding:10px 12px;text-align:right;font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.06em;font-weight:600;">Fresha</th>
          <th style="padding:10px 12px;text-align:right;font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.06em;font-weight:600;">Counted</th>
          <th style="padding:10px 12px;text-align:right;font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.06em;font-weight:600;">Variance</th>
          <th style="padding:10px 12px;text-align:center;font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.06em;font-weight:600;">Status</th>
        </tr>
      </thead>
      <tbody>{rows}
      </tbody>
    </table>
  </div>

  <div style="font-size:11px;color:#444444;text-align:center;">
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


def run():
    today_darwin     = datetime.now(DARWIN_TZ).date()
    yesterday_darwin = today_darwin - timedelta(days=1)
    date_str         = yesterday_darwin.strftime("%Y-%m-%d")

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
    run()
