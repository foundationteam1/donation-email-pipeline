"""One-time backfill for existing donations in the Notion Donations database.

Step 1 (relations): links every donation to all other donations with the same
        Donor Email, via the two-way self-relation.
Step 2 (template):  applies the database's DEFAULT template (the linked view
        of past donations) to pages whose body is empty. Pages that already
        have content are skipped, so re-running never duplicates the table.

Environment variables:
  NOTION_TOKEN, NOTION_DATABASE_ID   required
  DRY_RUN=1     only print what would be done
  LIMIT=5       process only the first N pages that need work (for testing)
  STEPS=both    'relations', 'template' or 'both' (default)

Run it once, first with LIMIT=5, check a couple of pages in Notion, then
without LIMIT. It is safe to re-run: finished pages are skipped.
"""
import os
import time
from collections import defaultdict

import requests

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DB = os.environ["NOTION_DATABASE_ID"]

DRY_RUN = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")
LIMIT = int(os.environ.get("LIMIT", "0") or 0)
STEPS = os.environ.get("STEPS", "both").strip().lower()

RELATED_PROPERTY = "Same Donor Donations"

H = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}
H_NEW = {**H, "Notion-Version": "2026-03-11"}

PAUSE = 0.35  # Notion allows roughly 3 requests per second


def call(method, url, **kwargs):
    """requests wrapper: throttles and retries on rate limiting (429)."""
    for attempt in range(6):
        response = requests.request(method, url, **kwargs)
        if response.status_code == 429:
            wait = float(response.headers.get("Retry-After", 2))
            print(f"  rate limited, waiting {wait:.0f}s")
            time.sleep(wait)
            continue
        time.sleep(PAUSE)
        return response
    return response


def load_pages():
    pages, cursor = [], None
    while True:
        body = {"page_size": 100,
                "sorts": [{"property": "Donation Date", "direction": "descending"}]}
        if cursor:
            body["start_cursor"] = cursor
        r = call("POST", f"https://api.notion.com/v1/databases/{DB}/query",
                 headers=H, json=body)
        r.raise_for_status()
        data = r.json()
        pages += data["results"]
        print(f"  loaded {len(pages)} pages...")
        if not data.get("has_more"):
            break
        cursor = data["next_cursor"]
    return pages


def email_of(page):
    prop = page["properties"].get("Donor Email") or {}
    return (prop.get("email") or "").strip().lower()


def current_relation(page):
    prop = page["properties"].get(RELATED_PROPERTY) or {}
    return {x["id"] for x in prop.get("relation", [])}


def step_relations(pages):
    groups = defaultdict(list)
    for p in pages:
        e = email_of(p)
        if e:
            groups[e].append(p)

    todo = []
    for email, group in groups.items():
        if len(group) < 2:
            continue
        ids = [p["id"] for p in group]
        for p in group:
            wanted = {i for i in ids if i != p["id"]}
            if len(wanted) > 100:
                wanted = set(list(wanted)[:100])
            if current_relation(p) != wanted:
                todo.append((p["id"], wanted))

    print(f"Relations: {len(todo)} page(s) to update "
          f"({len(groups)} distinct emails)")
    done = 0
    for page_id, wanted in todo:
        if LIMIT and done >= LIMIT:
            break
        if DRY_RUN:
            print(f"  DRY RUN: {page_id} -> {len(wanted)} related")
        else:
            r = call("PATCH", f"https://api.notion.com/v1/pages/{page_id}",
                     headers=H,
                     json={"properties": {RELATED_PROPERTY: {
                         "relation": [{"id": i} for i in wanted]}}})
            if r.status_code != 200:
                print("  relation error:", page_id, r.status_code, r.text)
                if r.status_code == 400:
                    raise SystemExit(
                        f"Check that the property '{RELATED_PROPERTY}' exists "
                        f"and is named exactly that.")
                continue
        done += 1
    print(f"Relations done: {done}")


def has_content(page_id):
    r = call("GET", f"https://api.notion.com/v1/blocks/{page_id}/children",
             headers=H, params={"page_size": 1})
    r.raise_for_status()
    return bool(r.json().get("results"))


def step_template(pages):
    done = skipped = 0
    for p in pages:
        if LIMIT and done >= LIMIT:
            break
        if has_content(p["id"]):
            skipped += 1
            continue
        if DRY_RUN:
            print(f"  DRY RUN: would apply template to {p['id']}")
        else:
            r = call("PATCH", f"https://api.notion.com/v1/pages/{p['id']}",
                     headers=H_NEW,
                     json={"template": {"type": "default",
                                        "timezone": "Europe/Kyiv"}})
            if r.status_code != 200:
                print("  template error:", p["id"], r.status_code, r.text)
                continue
        done += 1
        if done % 50 == 0:
            print(f"  template applied to {done} pages...")
    print(f"Template done: {done}, skipped (already had content): {skipped}")


def main():
    if DRY_RUN:
        print("=== DRY RUN: nothing will be changed ===")
    print("Loading pages...")
    pages = load_pages()
    print(f"Total pages: {len(pages)}")

    if STEPS in ("relations", "both"):
        step_relations(pages)
    if STEPS in ("template", "both"):
        step_template(pages)
    print("Finished.")


if __name__ == "__main__":
    main()
