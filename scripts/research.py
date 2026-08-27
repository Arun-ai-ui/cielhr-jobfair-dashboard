
import os
import time
from datetime import date, datetime
from urllib.parse import urlparse

import requests


TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]
TAVILY_RESEARCH_URL = "https://api.tavily.com/research"

EVENTS_FILE = "events.json"


RESEARCH_PROMPT = """
Find upcoming job fairs, employment fairs, recruitment fairs, career fairs,
and government Rojgar Melas happening in India.

The purpose is to help CIEL HR identify events where employers/recruiters
can participate and hire candidates.

Search across ALL regions of India, including:
North India, South India, East India, West India, Central India,
Northeast India, and all Union Territories.

Prioritize credible sources such as:
- Government of India
- National Career Service (NCS)
- State employment departments
- State skill-development departments
- District administrations
- Government universities
- Recognized industry bodies
- Established job portals
- Established event organizers

ONLY include events that are upcoming or genuinely recurring.

For events with a specific date, provide the date in YYYY-MM-DD format.

Do NOT invent dates, organizers, fees, registration deadlines, URLs,
or other information.

If a field cannot be verified, return null.

For recurring events where an exact upcoming date is not published,
you may mark the date as "Recurring — check portal".

Every event must have a source URL.

Look for newly announced events as well as changes to known upcoming events.

Return only events that are relevant to employer/recruiter participation.
"""


OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": ["string", "null"]},
                    "date": {"type": ["string", "null"]},
                    "region": {"type": ["string", "null"]},
                    "org": {"type": ["string", "null"]},
                    "fmt": {"type": ["string", "null"]},
                    "fee": {"type": ["string", "null"]},
                    "regClose": {"type": ["string", "null"]},
                    "url": {"type": ["string", "null"]},
                    "source": {"type": ["string", "null"]},
                },
                "required": [
                    "name",
                    "date",
                    "region",
                    "org",
                    "fmt",
                    "fee",
                    "regClose",
                    "url",
                    "source",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["events"],
    "additionalProperties": False,
}


def load_existing_events():
    if not os.path.exists(EVENTS_FILE):
        return []

    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("events.json must contain a JSON array.")

    return data


def normalize(value):
    if not value:
        return ""

    return " ".join(str(value).lower().strip().split())


def normalize_url(url):
    if not url:
        return ""

    url = str(url).strip()

    parsed = urlparse(url)

    if not parsed.scheme:
        return url.rstrip("/").lower()

    return (
        f"{parsed.scheme.lower()}://"
        f"{parsed.netloc.lower()}"
        f"{parsed.path.rstrip('/')}"
    )


def is_valid_url(url):
    if not url:
        return False

    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def is_past_event(event_date):
    if not event_date:
        return False

    if not isinstance(event_date, str):
        return False

    if not event_date[:4].isdigit():
        return False

    try:
        parsed = datetime.strptime(event_date, "%Y-%m-%d").date()
        return parsed < date.today()
    except ValueError:
        return False


def event_key(event):
    return (
        normalize(event.get("name")),
        normalize(event.get("date")),
    )


def clean_event(event):
    cleaned = {
        "name": event.get("name"),
        "date": event.get("date"),
        "region": event.get("region"),
        "org": event.get("org"),
        "fmt": event.get("fmt"),
        "fee": event.get("fee"),
        "regClose": event.get("regClose"),
        "url": event.get("url"),
        "source": event.get("source") or event.get("url"),
    }

    for key, value in cleaned.items():
        if isinstance(value, str):
            cleaned[key] = value.strip()

    return cleaned


def start_tavily_research():
    headers = {
        "Authorization": f"Bearer {TAVILY_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "input": RESEARCH_PROMPT,
        "output_schema": OUTPUT_SCHEMA,
    }

    response = requests.post(
        TAVILY_RESEARCH_URL,
        headers=headers,
        json=payload,
        timeout=60,
    )

    response.raise_for_status()

    data = response.json()

    request_id = data.get("request_id")

    if not request_id:
        raise RuntimeError(
            f"Tavily did not return a request_id. Response: {data}"
        )

    return request_id


def wait_for_research(request_id):
    headers = {
        "Authorization": f"Bearer {TAVILY_API_KEY}",
    }

    status_url = f"{TAVILY_RESEARCH_URL}/{request_id}"

    for attempt in range(30):
        response = requests.get(
            status_url,
            headers=headers,
            timeout=60,
        )

        response.raise_for_status()

        data = response.json()

        status = data.get("status")

        print(f"Tavily research status: {status}")

        if status == "completed":
            return data

        if status in ("failed", "cancelled"):
            raise RuntimeError(
                f"Tavily research failed: {data}"
            )

        time.sleep(10)

    raise TimeoutError("Tavily research did not finish within the timeout.")


def extract_events(result):
    content = result.get("content")

    if isinstance(content, dict):
        events = content.get("events", [])
    elif isinstance(content, str):
        parsed = json.loads(content)
        events = parsed.get("events", [])
    else:
        events = []

    if not isinstance(events, list):
        return []

    return events


def merge_events(existing_events, researched_events):
    existing_by_url = {}
    existing_by_key = {}

    for event in existing_events:
        url_key = normalize_url(event.get("url"))

        if url_key:
            existing_by_url[url_key] = event

        existing_by_key[event_key(event)] = event

    final_events = list(existing_events)

    new_count = 0
    updated_count = 0
    duplicate_count = 0

    for raw_event in researched_events:
        event = clean_event(raw_event)

        name = event.get("name")
        event_date = event.get("date")
        url = event.get("url")
        source = event.get("source")

        if not name:
            continue

        if not url or not is_valid_url(url):
            continue

        if not source or not is_valid_url(source):
            event["source"] = url

        if is_past_event(event_date):
            continue

        url_key = normalize_url(url)
        key = event_key(event)

        existing = existing_by_url.get(url_key)

        if existing is None:
            existing = existing_by_key.get(key)

        if existing is not None:
            duplicate_count += 1

            # Update only research-related information.
            for field in [
                "name",
                "date",
                "region",
                "org",
                "fmt",
                "fee",
                "regClose",
                "url",
                "source",
            ]:
                if event.get(field):
                    existing[field] = event[field]

            updated_count += 1
            continue

        new_event = {
            "id": f"auto-{len(final_events) + 1}",
            **event,
            "lastVerified": date.today().isoformat(),
        }

        final_events.append(new_event)

        existing_by_url[url_key] = new_event
        existing_by_key[key] = new_event

        new_count += 1

    # Make sure all events have a verification date.
    for event in final_events:
        if not event.get("lastVerified"):
            event["lastVerified"] = date.today().isoformat()

    # Put exact-date events first.
    final_events.sort(
        key=lambda e: (
            1 if not str(e.get("date", ""))[:4].isdigit() else 0,
            str(e.get("date", "")),
            normalize(e.get("name")),
        )
    )

    return final_events, new_count, updated_count, duplicate_count


def save_events(events):
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            events,
            f,
            ensure_ascii=False,
            indent=2,
        )

        f.write("\n")


def main():
    print("Loading existing events...")
    existing_events = load_existing_events()

    print(f"Existing events: {len(existing_events)}")

    print("Starting Tavily research...")
    request_id = start_tavily_research()

    print(f"Tavily request ID: {request_id}")

    print("Waiting for Tavily research to finish...")
    result = wait_for_research(request_id)

    researched_events = extract_events(result)

    print(f"Events returned by Tavily: {len(researched_events)}")

    final_events, new_count, updated_count, duplicate_count = merge_events(
        existing_events,
        researched_events,
    )

    save_events(final_events)

    print()
    print("========== REFRESH COMPLETE ==========")
    print(f"Existing events: {len(existing_events)}")
    print(f"Tavily results:  {len(researched_events)}")
    print(f"New events:      {new_count}")
    print(f"Updated events:  {updated_count}")
    print(f"Duplicates:      {duplicate_count}")
    print(f"Final events:    {len(final_events)}")
    print("=======================================")


if __name__ == "__main__":
    main()
