import os
import requests
from datetime import datetime, timedelta
import pytz

# --- CREDENTIALS FROM GITHUB SECRETS ---
ZOHO_CLIENT_ID = os.environ["ZOHO_CLIENT_ID"]
ZOHO_CLIENT_SECRET = os.environ["ZOHO_CLIENT_SECRET"]
ZOHO_REFRESH_TOKEN = os.environ["ZOHO_REFRESH_TOKEN"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]

ZOHO_TOKEN_URL = "https://accounts.zoho.eu/oauth/v2/token"
ZOHO_API_BASE = "https://www.zohoapis.eu/crm/v2"

KYIV_TZ = pytz.timezone("Europe/Kyiv")

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
    response.raise_for_status()
    return response.json()["access_token"]


def get_window():
    """
    Returns start and end of collection window in Kyiv time:
    10:00am yesterday → 10:00am today.
    """
    now_kyiv = datetime.now(KYIV_TZ)
    end = now_kyiv.replace(hour=10, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=1)
    print(f"Collection window: {start.isoformat()} → {end.isoformat()} (Kyiv time)")
    return start, end


def already_in_notion(donor_email, date_str):
    """Check if a row with this email and date already exists in Notion."""
    payload = {
        "filter": {
            "and": [
                {"property": "Donor Email", "email": {"equals": donor_email}},
                {"property": "Donation Date", "date": {"equals": date_str[:10]}}
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


def get_donor_status(access_token, contact_id):
    """Fetches Donor_status directly from the Contacts module using the contact ID."""
    if not contact_id:
        return ""
    
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    # Double-check if the API name is exactly 'Donor_status' in Zoho CRM Setup
    params = {"fields": "Donor_status"} 
    
    response = requests.get(
        f"{ZOHO_API_BASE}/Contacts/{contact_id}", 
        headers=headers, 
        params=params
    )
    if response.status_code != 200:
        print(f"Failed to fetch contact module status for {contact_id}: {response.status_code}")
        return ""
        
    data = response.json().get("data", [])
    if not data:
        return ""
    return data[0].get("Donor_status") or ""


def get_donations_in_window(access_token, start, end):
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    params = {
        "fields": "Contact_of_the_donor,Email,Donation_amount_in_USD,Date_of_donation",
        "per_page": 500
    }

    response = requests.get(
        f"{ZOHO_API_BASE}/Donations",
        headers=headers,
        params=params
    )

    if response.status_code == 204:
        return []

    if response.status_code != 200:
        print("Zoho fetch error:", response.status_code, response.text)
        response.raise_for_status()

    all_records = response.json().get("data", [])
    print(f"Total records fetched: {len(all_records)}")

    if all_records:
        print("Fields in first record:", list(all_records[0].keys()))

    filtered = []
    for r in all_records:
        date_str = r.get("Date_of_donation") or ""
        if not date_str:
            continue
        try:
            record_dt = datetime.fromisoformat(date_str)
            record_dt_kyiv = record_dt.astimezone(KYIV_TZ)
            if start <= record_dt_kyiv < end:
                filtered.append(r)
        except ValueError:
            print(f"Could not parse date: {date_str}")
            continue

    return filtered


def write_to_notion(donor_name, donor_email, amount, date, donor_status):
    properties = {
        "Donor Name": {
            "title": [{"text": {"content": donor_name}}]
        },
        "Donation Amount": {
            "number": float(amount)
        },
        "Approved": {
            "checkbox": False
        },
        "Email Sent": {
            "checkbox": False
        }
    }

    if donor_email:
        properties["Donor Email"] = {"email": donor_email}

    if date:
        properties["Donation Date"] = {"date": {"start": date}}

    if donor_status:
        properties["Donor Status"] = {"select": {"name": donor_status}}

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": properties
    }

    response = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=payload
    )

    if response.status_code != 200:
        print("Notion error:", response.status_code, response.text)
        response.raise_for_status()

    print(f"Written to Notion: {donor_name} — ${amount} — {date} — {donor_status}")


def main():
    print("Fetching Zoho access token...")
    access_token = get_zoho_access_token()

    start, end = get_window()

    print("Fetching donations in window...")
    donations = get_donations_in_window(access_token, start, end)
    print(f"Found {len(donations)} donation(s) in window")

    for donation in donations:
        amount = float(donation.get("Donation_amount_in_USD") or 0)
        donor_email = donation.get("Email") or ""
        date = donation.get("Date_of_donation") or ""

        # Safe extraction of Contact data lookup
        contact_lookup = donation.get("Contact_of_the_donor")
        contact_id = None
        donor_name = "Unknown"

        if isinstance(contact_lookup, dict):
            contact_id = contact_lookup.get("id")
            donor_name = contact_lookup.get("name", "Unknown")

        # Fetch the status from the Contact record since it doesn't exist on the Donation
        print(f"Fetching status for Contact ID: {contact_id}...")
        donor_status = get_donor_status(access_token, contact_id)

        # Skip if already written to Notion (prevents duplicates from two cron runs)
        if donor_email and already_in_notion(donor_email, date):
            print(f"Skipping {donor_name} — already in Notion")
            continue

        write_to_notion(donor_name, donor_email, amount, date, donor_status)

    print("Done.")


if __name__ == "__main__":
    main()
