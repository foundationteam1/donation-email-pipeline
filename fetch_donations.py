def get_yesterday_donations(access_token):
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    params = {
        "fields": "Contact_of_the_donor,Email,Donation_amount_in_USD,Date_of_donation,Donor_status",
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

    filtered = [
        print(f"Looking for date: {yesterday}")
        print(f"Sample dates from Zoho: {[r.get('Date_of_donation') for r in all_records[:5]]}")
        r for r in all_records
        if (r.get("Date_of_donation") or "").startswith(yesterday)
    ]

    return filtered
