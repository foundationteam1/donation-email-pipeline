
import os
import sys
import base64
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pytz
import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
# --- TIMEZONE CHECK ---
# Script runs at 13:00 and 14:00 UTC to cover winter/summer Kyiv time.
# Only proceeds if it is currently 16:00 (4pm) in Kyiv.
KYIV_TZ = pytz.timezone("Europe/Kyiv")
SEND_HOUR_KYIV = 16
now_kyiv = datetime.now(KYIV_TZ)
#if now_kyiv.hour != SEND_HOUR_KYIV:
#    print(f"Current Kyiv time is {now_kyiv.strftime('%H:%M')} — not {SEND_HOUR_KYIV}:00, exiting.")
#    sys.exit(0)
print(f"Kyiv time is {now_kyiv.strftime('%H:%M')} — proceeding.")
# --- SENDER EMAIL ADDRESSES ---
SVITLANA_EMAIL = "sdenysenko@kse.org.ua"
TYMOFIY_EMAIL = "president@kse.org.ua"  # Fill in when ready
# --- CREDENTIALS FROM GITHUB SECRETS ---
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
GMAIL_SVITLANA_CLIENT_ID = os.environ["GMAIL_SVITLANA_CLIENT_ID"]
GMAIL_SVITLANA_CLIENT_SECRET = os.environ["GMAIL_SVITLANA_CLIENT_SECRET"]
GMAIL_SVITLANA_REFRESH_TOKEN = os.environ["GMAIL_SVITLANA_REFRESH_TOKEN"]
GMAIL_TYMOFIY_CLIENT_ID = os.environ["GMAIL_TYMOFIY_CLIENT_ID"]
GMAIL_TYMOFIY_CLIENT_SECRET = os.environ["GMAIL_TYMOFIY_CLIENT_SECRET"]
GMAIL_TYMOFIY_REFRESH_TOKEN = os.environ["GMAIL_TYMOFIY_REFRESH_TOKEN"]
# --- TELEGRAM CREDENTIALS ---
# Reuse the token of the bot you already have running in the group.
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SVITLANA_TELEGRAM_USER_ID = os.environ["SVITLANA_TELEGRAM_USER_ID"]
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}
def get_gmail_service(client_id, client_secret, refresh_token):
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/gmail.compose"]
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)
def get_approved_rows():
    """Fetch Notion rows where Approved=true and Draft Created=false."""
    payload = {
        "filter": {
            "and": [
                {"property": "Approved", "checkbox": {"equals": True}},
                {"property": "Draft Created", "checkbox": {"equals": False}}
            ]
        }
    }
    response = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=NOTION_HEADERS,
        json=payload
    )
    response.raise_for_status()
    return response.json().get("results", [])
def was_emailed_recently(email_sent_at_str):
    """Returns True if Email Sent At is within the last 60 days."""
    if not email_sent_at_str:
        return False
    try:
        sent_at = datetime.fromisoformat(email_sent_at_str)
        # Make timezone-aware if needed
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=60)
        return sent_at > cutoff
    except ValueError:
        return False
def get_first_name(full_name):
    """Takes the first word of the full name and capitalizes it."""
    if not full_name:
        return "Friend"
    return full_name.strip().split()[0].capitalize()
def create_draft(service, sender_email, recipient_email, donor_name):
    first_name = get_first_name(donor_name)
    subject = "Thank you for your donation to KSE Foundation"
    html_body = f"""<html>
<body>
  <p>Dear {first_name},</p>
  <p>Thank you for your support of KSE.</p>
  <p>KSE is now #1 in Ukraine by the average admission score of enrolled bachelor students.</p>
  <p>KSE educates 0.2% of Ukraine’s university students, and this year 12.2% of the country’s STEM olympiad winners chose KSE.</p>
  <p>For us it means that KSE is attracting some of the best young talent in the country.</p>
  <p>This is possible because of partners like you. Your support allows us to give talented students both the opportunity and the confidence to build their future in Ukraine.</p>
  <p>Our students can study, and KSE can continue operating fully, only because Ukraine’s defenders make it possible. We remain fully committed to supporting them.</p>
  <p>Thank you for being with KSE.</p>
  <p>Warm regards,<br>
  Svitlana Denysenko<br>
  Executive Director, KSE Foundation</p>
</body>
</html>"""
    message = MIMEMultipart("alternative")
    message["to"] = recipient_email
    message["from"] = sender_email
    message["subject"] = subject
    message.attach(MIMEText(html_body, "html", "utf-8"))
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().drafts().create(
        userId="me",
        body={"message": {"raw": encoded}}
    ).execute()
    print(f"Draft created for {recipient_email} (from {sender_email})")
def mark_draft_created(page_id):
    payload = {
        "properties": {
            "Draft Created": {"checkbox": True},
            "Email Sent At": {"date": {"start": datetime.now(timezone.utc).isoformat()}}
        }
    }
    response = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json=payload
    )
    if response.status_code != 200:
        print(f"Failed to update Notion row {page_id}: {response.status_code} {response.text}")
        response.raise_for_status()
def send_telegram_notification():
    """Post a message to the group, tagging Svitlana via her numeric user ID.
    She has no @username, so we use a text mention: linking her name to
    tg://user?id=<id> makes it a tappable mention that notifies her.
    """
    text = (
        f'<a href="tg://user?id={SVITLANA_TELEGRAM_USER_ID}">Світлано</a>, '
        'добрий день! Драфти створені на пошті.'
    )
    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }
    )
    # Drafts are already created at this point, so just log on failure — don't crash the job.
    if response.status_code != 200:
        print(f"Failed to send Telegram message: {response.status_code} {response.text}")
    else:
        print("Telegram notification sent.")
def main():
    print("Fetching approved rows from Notion...")
    rows = get_approved_rows()
    print(f"Found {len(rows)} approved row(s) with no draft yet")
    # Only initialize Tymofiy service if email is configured
    svitlana_service = get_gmail_service(
        GMAIL_SVITLANA_CLIENT_ID, GMAIL_SVITLANA_CLIENT_SECRET, GMAIL_SVITLANA_REFRESH_TOKEN
    )
    tymofiy_service = None
    if TYMOFIY_EMAIL:
        tymofiy_service = get_gmail_service(
            GMAIL_TYMOFIY_CLIENT_ID, GMAIL_TYMOFIY_CLIENT_SECRET, GMAIL_TYMOFIY_REFRESH_TOKEN
        )
    drafts_created = 0
    for row in rows:
        props = row["properties"]
        page_id = row["id"]
        # Extract fields
        donor_name = props["Donor Name"]["title"][0]["text"]["content"] if props["Donor Name"]["title"] else "Donor"
        donor_email = props["Donor Email"]["email"] or "" if props["Donor Email"]["email"] else ""
        amount = props["Donation Amount"]["number"] or 0
        donor_status = props["Donor Status"]["select"]["name"] if props["Donor Status"]["select"] else ""
        email_sent_at = props["Email Sent At"]["date"]["start"] if props["Email Sent At"]["date"] else ""
        print(f"Processing: {donor_name} — ${amount} — status: {donor_status}")
        if not donor_email:
            print(f"  Skipping — no email address")
            continue
        # --- ROUTING LOGIC ---
        if amount < 100:
            if donor_status != "New":
                print(f"  Skipping — under $100 but donor status is '{donor_status}', not New")
                continue
            create_draft(svitlana_service, SVITLANA_EMAIL, donor_email, donor_name)
            mark_draft_created(page_id)
            drafts_created += 1
        elif amount < 1000:
            if was_emailed_recently(email_sent_at):
                print(f"  Skipping — emailed within last 60 days")
                continue
            create_draft(svitlana_service, SVITLANA_EMAIL, donor_email, donor_name)
            mark_draft_created(page_id)
            drafts_created += 1
        else:
            # $1000+
            if not TYMOFIY_EMAIL or not tymofiy_service:
                print(f"  Skipping — Tymofiy email not configured yet")
                continue
            create_draft(tymofiy_service, TYMOFIY_EMAIL, donor_email, donor_name)
            mark_draft_created(page_id)
            drafts_created += 1
    # --- NOTIFY SVITLANA AFTER ALL EMAILS ARE DONE ---
    if drafts_created > 0:
        send_telegram_notification()
    else:
        print("No drafts created — skipping Telegram notification.")
    print("Done.")
if __name__ == "__main__":
    main()
