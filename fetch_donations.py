import os
import requests
from datetime import datetime, timedelta

# --- CREDENTIALS FROM GITHUB SECRETS ---
ZOHO_CLIENT_ID = os.environ["ZOHO_CLIENT_ID"]
ZOHO_CLIENT_SECRET = os.environ["ZOHO_CLIENT_SECRET"]
ZOHO_REFRESH_TOKEN = os.environ["ZOHO_REFRESH_TOKEN"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

ZOHO_TOKEN_URL = "https://accounts.zoho.eu/oauth/v2/token"
ZOHO_API_BASE = "https://www.zohoapis.eu/crm/v2"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}


def get_zoho_access_token():
    response = requests.post(ZOHO_TOKEN_URL, params={
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token"
    })
    print("Zoho token response:", response.json())  # temporary debug line
    response.raise_for_status()
    return response.json()["access_token"]


def get_yesterday_donations(access_token):
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.utcnow().strftime("%Y-%m-%d")

    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    params = {
        "fields": "Contact_of_the_donor,Email,Donation_amount_in_USD,Date_of_donation",
        "criteria": f"(Date_of_donation:between:{yesterday},{today})"
    }

    response = requests.get(
        f"{ZOHO_API_BASE}/Donations/search",
        headers=headers,
        params=params
    )

    if response.status_code == 204:
        return []

    response.raise_for_status()
    return response.json().get("data", [])


def write_to_notion(donor_name, donor_email, amount, date):
    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Donor Name": {
                "title": [{"text": {"content": donor_name}}]
            },
            "Donor Email": {
                "email": donor_email
            },
            "Donation Amount": {
                "number": float(amount)
            },
            "Donation Date": {
                "date": {"start": date}
            },
            "Approved": {
                "checkbox": False
            },
            "Email Sent": {
                "checkbox": False
            }
        }
    }

    response = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=payload
    )
    response.raise_for_status()
    print(f"Written to Notion: {donor_name} — ${amount} — {date}")


def main():
    print("Fetching Zoho access token...")
    access_token = get_zoho_access_token()

    print("Fetching yesterday's donations...")
    donations = get_yesterday_donations(access_token)
    print(f"Found {len(donations)} donation(s)")

    for donation in donations:
        amount = float(donation.get("Donation_amount_in_USD", 0))
        donor_email = donation.get("Email", "")
        date = donation.get("Date_of_donation", "")

        contact = donation.get("Contact_of_the_donor", {})
        donor_name = contact.get("name", "Unknown") if isinstance(contact, dict) else "Unknown"

        write_to_notion(donor_name, donor_email, amount, date)

    print("Done.")


if __name__ == "__main__":
    main()
