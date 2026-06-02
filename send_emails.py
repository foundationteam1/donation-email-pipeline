import os
import base64
from datetime import datetime, timedelta
from email.mime.text import MIMEText

import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# --- SEND MODE: set to "draft" to create drafts, "send" to send immediately ---
SEND_MODE = "draft"

# --- SENDER EMAIL ADDRESSES ---
SVITLANA_EMAIL = "sdenysenko@kse.org.ua"
TYMOFIY_EMAIL = ""  # Fill in when ready

# --- CREDENTIALS FROM GITHUB SECRETS ---
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

GMAIL_SVITLANA_CLIENT_ID = os.environ["GMAIL_SVITLANA_CLIENT_ID"]
GMAIL_SVITLANA_CLIENT_SECRET = os.environ["GMAIL_SVITLANA_CLIENT_SECRET"]
GMAIL_SVITLANA_REFRESH_TOKEN = os.environ["GMAIL_SVITLANA_REFRESH_TOKEN"]

GMAIL_TYMOFIY_CLIENT_ID = os.environ["GMAIL_TYMOFIY_CLIENT_ID"]
GMAIL_TYMOFIY_CLIENT_SECRET = os.environ["GMAIL_TYMOFIY_CLIENT_SECRET"]
GMAIL_TYMOFIY_REFRESH_TOKEN = os.environ["GMAIL_TYMOFIY_REFRESH_TOKEN"]

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
        scopes=["https://www.googleapis.com/auth/gmail.send",
                "https://www.googleapis.com/auth/gmail.compose"]
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


def get_pending_approved_rows():
    """Fetch Notion rows where Approved=true and Email Sent=false."""
    payload = {
        "filter": {
            "and": [
                {"property": "Approved", "checkbox": {"equals": True}},
                {"property": "Email Sent", "checkbox": {"equals": False}}
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


def was_emailed_in_last_60_days(donor_email):
    """Check Notion log for emails sent to this donor in the last 60 days."""
    cutoff = (datetime.utcnow() - timedelta(days=60)).isoformat()

    payload = {
        "filter": {
            "and": [
                {"property": "Donor Email", "email": {"equals": donor_email}},
                {"property": "Email Sent", "checkbox": {"equals": True}},
                {"property": "Email Sent At", "date": {"after": cutoff}}
            ]
        }
    }

    response = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=NOTION_HEADERS,
        json=payload
    )
    response.raise_for_status()
    return len(response.json().get("results", [])) > 0


def build_message(sender_email, recipient_email, donor_name, amount):
    subject = "Thank you for your donation"
    body = (
        f"Dear {donor_name},\n\n"
        f"Thank you so much for your generous donation of ${amount:.2f}. "
        f"Your support means a great deal to us.\n\n"
        f"With gratitude,\n"
        f"Kyiv School of Economics"
    )
    message = MIMEText(body)
    message["to"] = recipient_email
    message["from"] = sender_email
    message["subject"] = subject
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def send_or_draft(service, sender_email, recipient_email, donor_name, amount):
    encoded = build_message(sender_email, recipient_email, donor_name, amount)

    if SEND_MODE == "draft":
        service.users().drafts().create(
            userId="me",
            body={"message": {"raw": encoded}}
        ).execute()
        print(f"Draft created for {recipient_email} (from {sender_email})")
    else:
        service.users().messages().send(
            userId="me",
            body={"raw": encoded}
        ).execute()
        print(f"Email sent to {recipient_email} from {sender_email}")


def mark_email_sent(page_id, sender_name):
    payload = {
        "properties": {
            "Email Sent": {"checkbox": True},
            "Email Sent At": {"date": {"start": datetime.utcnow().isoformat()}},
            "Status": {"select": {"name": "Email Sent"}}
        }
    }
    requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json=payload
    ).raise_for_status()


def mark_skipped(page_id, reason):
    payload = {
        "properties": {
            "Status": {"select": {"name": reason}}
        }
    }
    requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json=payload
    ).raise_for_status()


def main():
    print("Fetching approved rows from Notion...")
    rows = get_pending_approved_rows()
    print(f"Found {len(rows)} approved row(s) pending email")

    svitlana_service = get_gmail_service(
        GMAIL_SVITLANA_CLIENT_ID, GMAIL_SVITLANA_CLIENT_SECRET, GMAIL_SVITLANA_REFRESH_TOKEN
    )
    tymofiy_service = get_gmail_service(
        GMAIL_TYMOFIY_CLIENT_ID, GMAIL_TYMOFIY_CLIENT_SECRET, GMAIL_TYMOFIY_REFRESH_TOKEN
    )

    for row in rows:
        props = row["properties"]
        page_id = row["id"]

        donor_name = props["Donor Name"]["title"][0]["text"]["content"] if props["Donor Name"]["title"] else "Donor"
        donor_email = props["Donor Email"]["email"] or ""
        amount = props["Donation Amount"]["number"] or 0
        recurring = props["Recurring donor"]["checkbox"]
        tier = props["Email Tier"]["select"]["name"] if props["Email Tier"]["select"] else ""
        status = props["Status"]["select"]["name"] if props["Status"]["select"] else ""

        # Skip rows already processed
        if "Skipped" in status or status == "Email Sent":
            print(f"Skipping {donor_name} — already marked as {status}")
            continue

        if not donor_email:
            print(f"Skipping {donor_name} — no email address")
            continue

        # Routing logic
        if amount < 100:
            if recurring:
                # Fundraise Up handles this
                print(f"Skipping {donor_name} — recurring donor under $100, Fundraise Up handles it")
                mark_skipped(page_id, "Skipped — Recurring Donor")
            else:
                send_or_draft(svitlana_service, SVITLANA_EMAIL, donor_email, donor_name, amount)
                mark_email_sent(page_id, "Svitlana")

        elif amount < 1000:
            if was_emailed_in_last_60_days(donor_email):
                print(f"Skipping {donor_name} — emailed within last 60 days")
                mark_skipped(page_id, "Skipped — 60 Day Rule")
            else:
                send_or_draft(svitlana_service, SVITLANA_EMAIL, donor_email, donor_name, amount)
                mark_email_sent(page_id, "Svitlana")

        else:
            # $1000+
            send_or_draft(tymofiy_service, TYMOFIY_EMAIL, donor_email, donor_name, amount)
            mark_email_sent(page_id, "Tymofiy")

    print("Done.")


if __name__ == "__main__":
    main()
