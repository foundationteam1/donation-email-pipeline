import os
import requests
from datetime import datetime, timedelta
import pytz
from translitua import translit, UkrainianKMU, UkrainianBGN

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

# =====================================================================
# UKRAINIAN -> LATIN NAME TRANSLITERATION
# ---------------------------------------------------------------------
# Uses the `translitua` library (pip install translitua).
#
#   STYLE = "passport" -> KMU 2010 official   (Sofiia, Andrii, Tymofii)
#                         matches Ukrainian ID documents / wire records
#   STYLE = "natural"  -> BGN/PCGN romanization (Sofiya, Andriy, Tymofiy)
#                         reads more naturally in donor-facing greetings
#
# Latin names (foreign donors, or names already entered in English) are
# left unchanged by the library, so it is safe to run on every record.
# =====================================================================
STYLE = "passport"
_STANDARD = {"passport": UkrainianKMU, "natural": UkrainianBGN}[STYLE]

# Hand-set spellings for known / major donors. These OVERRIDE both standards,
# for people whose established English spelling differs from the mechanical
# result (e.g. Тимофій -> auto "Tymofii", but he spells it "Tymofiy").
# Key by the exact Cyrillic name as it appears in Zoho.
NAME_OVERRIDES = {
    "Тимофій Милованов": "Tymofiy Mylovanov",
}


def to_english_name(name):
    """Override table first, otherwise transliterate with the chosen standard."""
    if not name:
        return name
    key = name.strip()
    if key in NAME_OVERRIDES:
        return NAME_OVERRIDES[key]
    return translit(name, _STANDARD)


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
    Returns start and end of collection window:
    24 hours ago → now, both in Kyiv time.
    Simple and reliable regardless of what time the script runs.
    """
    now_kyiv = datetime.now(KYIV_TZ)
    start = now_kyiv - timedelta(hours=24)
    print(f"Collection window: {start.isoformat()} → {now_kyiv.isoformat()} (Kyiv time)")
    return start, now_kyiv


def get_donor_status(access_token, contact_id):
    if not contact_id:
        return ""

    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    response = requests.get(
        f"{ZOHO_API_BASE}/Contacts/{contact_id}",
        headers=headers,
        params={"fields": "Donor_status"}
    )

    if response.status_code != 200:
        print(f"Failed to fetch contact status for {contact_id}: {response.status_code}")
        return ""

    data = response.json().get("data", [])
    if not data:
        return ""
    return data[0].get("Donor_status") or ""


def get_donations_in_window(access_token, start, end):
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    params = {
        "fields": "Contact_of_the_donor,Email,Donation_amount_in_USD,Date_of_donation,Designations,SOURCE",
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


def _to_dt(value):
    """Parse an ISO date/datetime string to a datetime, or return None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def donation_already_in_notion(donor_email, date):
    """Return True if a donation with the same donor email AND the same donation
    timestamp is already in Notion. Lets the script be re-run safely (e.g. while
    testing) without creating duplicate pages.

    Matching is on the FULL donation date/time, not just the calendar day, so two
    separate gifts from the same donor on the same day are kept as distinct
    records. Timestamps are compared as parsed instants, so a difference in
    formatting (fractional seconds, +00:00 vs Z) between what Zoho sends and what
    Notion returns won't cause a real duplicate to be missed. Uses the existing
    'Donor Email' and 'Donation Date' properties — no extra Notion column needed.
    """
    if not donor_email or not date:
        return False

    target = _to_dt(date)
    start_cursor = None

    while True:
        payload = {
            "filter": {"property": "Donor Email", "email": {"equals": donor_email}},
            "page_size": 100
        }
        if start_cursor:
            payload["start_cursor"] = start_cursor

        response = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=NOTION_HEADERS,
            json=payload
        )

        if response.status_code != 200:
            print("Notion query error:", response.status_code, response.text)
            response.raise_for_status()

        data = response.json()
        for page in data.get("results", []):
            existing = page.get("properties", {}).get("Donation Date", {}).get("date")
            existing_start = (existing or {}).get("start") or ""
            existing_dt = _to_dt(existing_start)
            if existing_start == date or (
                target is not None and existing_dt is not None and existing_dt == target
            ):
                return True

        if not data.get("has_more"):
            return False
        start_cursor = data.get("next_cursor")


def write_to_notion(donor_name, donor_email, amount, date, donor_status, designation, utm):
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
        "Draft Created": {
            "checkbox": False
        }
    }

    if donor_email:
        properties["Donor Email"] = {"email": donor_email}

    if date:
        properties["Donation Date"] = {"date": {"start": date}}

    if donor_status:
        properties["Donor Status"] = {"select": {"name": donor_status}}

    if designation:
        properties["Designation"] = {"rich_text": [{"text": {"content": designation}}]}

    if utm:
        properties["UTM"] = {"rich_text": [{"text": {"content": utm}}]}

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

    print(f"Written to Notion: {donor_name} — ${amount} — {date} — {donor_status} — {designation} — {utm}")


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

        # Skip donations already in Notion — same donor email + same timestamp (safe to re-run).
        if donation_already_in_notion(donor_email, date):
            print(f"Skipping duplicate donation ({donor_email} at {date})")
            continue

        contact_lookup = donation.get("Contact_of_the_donor")
        contact_id = None
        donor_name = "Unknown"

        if isinstance(contact_lookup, dict):
            contact_id = contact_lookup.get("id")
            donor_name = contact_lookup.get("name", "Unknown")

        # Transliterate Ukrainian Cyrillic names to Latin before writing.
        original_name = donor_name
        donor_name = to_english_name(donor_name)
        if donor_name != original_name:
            print(f"Transliterated: {original_name} -> {donor_name}")

        print(f"Fetching status for Contact ID: {contact_id}...")
        donor_status = get_donor_status(access_token, contact_id)

        designation_raw = donation.get("Designations")
        if isinstance(designation_raw, dict):
            designation = designation_raw.get("name") or ""
        elif isinstance(designation_raw, list):
            designation = ", ".join(d.get("name", "") for d in designation_raw if isinstance(d, dict))
        else:
            designation = str(designation_raw) if designation_raw else ""

        utm = donation.get("SOURCE") or ""

        write_to_notion(donor_name, donor_email, amount, date, donor_status, designation, utm)

    print("Done.")


if __name__ == "__main__":
    main()
