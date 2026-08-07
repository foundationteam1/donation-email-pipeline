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
# COLLECTION WINDOW
# ---------------------------------------------------------------------
# The script looks back LOOKBACK_DAYS days from the moment it runs.
# A window wider than the run interval is deliberate: if a run fails or
# GitHub delays the schedule, the next run still picks up what was missed.
# Duplicates are prevented by the dedup keys below, not by the window.
# =====================================================================
LOOKBACK_DAYS = 7

# Zoho paging. per_page is capped at 200 by the API regardless of what we ask.
ZOHO_PAGE_SIZE = 200
ZOHO_MAX_PAGES = 25
# Records are paged newest-created first. We keep paging until we are this far
# past the window start, so back-dated entries created late are still caught.
ZOHO_CREATED_GRACE_DAYS = 7

# =====================================================================
# UKRAINIAN -> LATIN NAME TRANSLITERATION
# ---------------------------------------------------------------------
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


# ---------------------------------------------------------------------
# DATE HELPERS
# ---------------------------------------------------------------------

def _parse_dt(value):
    """Parse an ISO date or datetime string into an aware datetime, or None.

    Naive values (a bare 'YYYY-MM-DD', or a datetime with no offset) are read as
    Kyiv time. Zoho and Notion both live in the foundation's timezone, and the
    GitHub runner is on UTC, so assuming the runner's clock would shift every
    date-only value back by three hours.
    """
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = KYIV_TZ.localize(dt)
    return dt


def _instant(value):
    """Normalise a timestamp to a single canonical string (UTC ISO).

    Zoho sends '2026-08-04T10:00:00+03:00'; Notion may hand the same instant
    back as '2026-08-04T07:00:00.000Z'. Both collapse to the same string here,
    so formatting differences can never hide a real duplicate.
    """
    dt = _parse_dt(value)
    return dt.astimezone(pytz.UTC).isoformat() if dt else ""


def donation_keys(donor_name, donor_email, amount, date_value):
    """Build the set of identity keys for one donation.

    Two keys, so a donation is recognised whether or not it has an email:
      - email  + exact instant
      - name   + amount + exact instant

    A donation counts as already-seen if EITHER key matches something in Notion.
    Matching is on the full timestamp, not the calendar day, so two separate
    gifts from the same donor on the same day stay distinct records.
    """
    ts = _instant(date_value) or "no-date"
    keys = set()
    if donor_email:
        keys.add(("email", donor_email.strip().lower(), ts))
    keys.add(("name", (donor_name or "").strip().lower(), round(float(amount or 0), 2), ts))
    return keys


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
    """Returns start and end of the collection window, both in Kyiv time."""
    now_kyiv = datetime.now(KYIV_TZ)
    start = now_kyiv - timedelta(days=LOOKBACK_DAYS)
    print(f"Collection window ({LOOKBACK_DAYS} days): "
          f"{start.isoformat()} → {now_kyiv.isoformat()} (Kyiv time)")
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
    """Page through Zoho Donations newest-first and keep those inside the window.

    The old version requested a single page and filtered it in Python, so any
    donation past the first 200 records was silently invisible. This walks pages
    until it is clearly past the window, then stops.
    """
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    fields = ("Contact_of_the_donor,Email,Donation_amount_in_USD,"
              "Date_of_donation,Designations,SOURCE")
    created_cutoff = start - timedelta(days=ZOHO_CREATED_GRACE_DAYS)

    collected = []
    seen_total = 0
    page = 1
    use_sort = True

    while page <= ZOHO_MAX_PAGES:
        params = {"fields": fields, "per_page": ZOHO_PAGE_SIZE, "page": page}
        if use_sort:
            params["sort_by"] = "Created_Time"
            params["sort_order"] = "desc"

        response = requests.get(f"{ZOHO_API_BASE}/Donations", headers=headers, params=params)

        # Some Zoho setups reject sorting on this module; retry unsorted once.
        if response.status_code == 400 and use_sort and page == 1:
            print("Zoho rejected sort parameters, retrying without them")
            use_sort = False
            continue

        if response.status_code == 204:
            break

        if response.status_code != 200:
            print("Zoho fetch error:", response.status_code, response.text)
            response.raise_for_status()

        body = response.json()
        records = body.get("data", [])
        if not records:
            break

        seen_total += len(records)
        oldest_created = None

        for r in records:
            record_dt = _parse_dt(r.get("Date_of_donation"))
            if record_dt and start <= record_dt < end:
                collected.append(r)

            created = _parse_dt(r.get("Created_Time"))
            if created and (oldest_created is None or created < oldest_created):
                oldest_created = created

        # Newest-first, so once a whole page predates the window we are done.
        if use_sort and oldest_created and oldest_created < created_cutoff:
            break

        if not body.get("info", {}).get("more_records"):
            break

        page += 1

    if page > ZOHO_MAX_PAGES:
        print(f"WARNING: stopped at the {ZOHO_MAX_PAGES}-page safety limit — "
              f"older donations in the window may have been missed")

    print(f"Scanned {seen_total} Zoho record(s) across {page} page(s)")
    return collected


def load_existing_keys(since):
    """Fetch everything already in Notion from `since` onward, as dedup keys.

    One paginated query per run, instead of a fresh query per donation. With a
    7-day window most donations are re-seen six times before they age out, so
    the old per-donation lookup meant hundreds of Notion calls a day.
    """
    keys = set()
    pages = 0
    start_cursor = None

    while True:
        payload = {
            "filter": {
                "property": "Donation Date",
                "date": {"on_or_after": since.date().isoformat()}
            },
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
            props = page.get("properties", {})

            email = (props.get("Donor Email") or {}).get("email") or ""
            date_prop = (props.get("Donation Date") or {}).get("date") or {}
            amount = (props.get("Donation Amount") or {}).get("number")
            title_parts = (props.get("Donor Name") or {}).get("title") or []
            name = "".join(part.get("plain_text", "") for part in title_parts)

            keys |= donation_keys(name, email, amount, date_prop.get("start"))
            pages += 1

        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")

    print(f"Loaded {pages} existing Notion record(s) → {len(keys)} dedup key(s)")
    return keys


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

    # One day of slack on the Notion side, so a donation sitting right on the
    # window edge is still recognised despite any timezone rounding.
    print("Loading donations already in Notion...")
    existing_keys = load_existing_keys(start - timedelta(days=1))

    print("Fetching donations in window...")
    donations = get_donations_in_window(access_token, start, end)
    print(f"Found {len(donations)} donation(s) in window")

    written = 0
    skipped = 0

    for donation in donations:
        amount = float(donation.get("Donation_amount_in_USD") or 0)
        donor_email = donation.get("Email") or ""
        date = donation.get("Date_of_donation") or ""

        contact_lookup = donation.get("Contact_of_the_donor")
        contact_id = None
        donor_name = "Unknown"

        if isinstance(contact_lookup, dict):
            contact_id = contact_lookup.get("id")
            donor_name = contact_lookup.get("name", "Unknown")

        # Transliterate before the dedup check: Notion stores the Latin form,
        # so both sides of the comparison have to be in the same alphabet.
        original_name = donor_name
        donor_name = to_english_name(donor_name)
        if donor_name != original_name:
            print(f"Transliterated: {original_name} -> {donor_name}")

        if not date:
            print(f"WARNING: {donor_name} has no donation date — "
                  f"dedup falls back to name + amount only")

        keys = donation_keys(donor_name, donor_email, amount, date)
        if keys & existing_keys:
            print(f"Skipping duplicate donation ({donor_name}, {donor_email or 'no email'} at {date})")
            skipped += 1
            continue

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

        # Register immediately, so an exact repeat inside this same batch is
        # caught too — the old code only compared against what Notion held
        # when the run started.
        existing_keys |= keys
        written += 1

    print(f"Done. Written: {written}, skipped as duplicates: {skipped}")


if __name__ == "__main__":
    main()
