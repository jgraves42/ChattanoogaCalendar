#!/usr/bin/env python3
"""
Chattanooga Events Calendar — scraper
Run:  python scripts/scraper.py
Writes updated data/events.json to the repo root.
"""
import hashlib
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import extruct
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# ── Paths ─────────────────────────────────────────────────────────────────────
JS_FILE = Path(__file__).parent.parent / "data" / "events.js"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── HTTP ──────────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
REQUEST_DELAY = 0.8   # seconds between requests


def fetch(url):
    try:
        r = SESSION.get(url, timeout=15)
        r.raise_for_status()
        time.sleep(REQUEST_DELAY)
        return r
    except Exception as e:
        log.warning(f"    fetch failed {url}: {e}")
        return None


def soup(url, resp=None):
    r = resp or fetch(url)
    return BeautifulSoup(r.text, "lxml") if r else None


def jsonld(url, html=None):
    """Return all JSON-LD items from a page, flattening @graph arrays."""
    if html is None:
        r = fetch(url)
        html = r.text if r else ""
    if not html:
        return []
    try:
        data = extruct.extract(html, base_url=url, syntaxes=["json-ld"])
        flat = []
        for item in data.get("json-ld", []):
            flat.extend(item.get("@graph", [item]))
        return flat
    except Exception:
        return []


# ── Normalisation ─────────────────────────────────────────────────────────────

# Venue name fragments that strongly imply a category
VENUE_HINTS = {
    "Music":  {"tavern", "bar", "brewery", "distillery", "lounge", "pub",
               "music hall", "amphitheater", "amphitheatre", "concert hall",
               "nightclub", "club", "records", "auditorium", "arena",
               "stage", "sound", "jazz", "rhythm", "groove", "juke"},
    "Arts":   {"gallery", "museum", "theater", "theatre", "cinema", "playhouse",
               "arts center", "art center", "studio", "comedy club"},
    "Food":   {"restaurant", "cafe", "bistro", "grill", "eatery", "food hall",
               "kitchen", "bakery", "wine bar", "taproom"},
    "Family": {"zoo", "aquarium", "park", "museum of", "children"},
}

CATEGORY_KEYWORDS = {
    "Music": [
        "concert", "music", "band", "live music", "jazz", "bluegrass",
        "orchestra", "symphony", "dj set", "dj ", "festival", "singer",
        "guitarist", "drummer", "vocalist", "rapper", "hip hop", "hip-hop",
        "r&b", "country music", "rock show", "punk", "metal", "indie",
        "folk", "acoustic", "open mic", "karaoke", "album", "ep release",
        "record release", "tour stop", "performing live", "live performance",
        "live show", "on stage", "live at", "setlist", "soundcheck",
        "tickets on sale", "stream ", "listening party", "vinyl night",
        "singer-songwriter", "songwriter", "musician", "performers",
        "headliner", "opening act", "tribute band", "cover band",
    ],
    "Arts": [
        "art show", "gallery", "exhibit", "exhibition", "theatre", "theater",
        "dance", "ballet", "opera", "film screening", "movie", "comedy show",
        "stand-up", "standup", "stand up", "comedian", "improv", "sketch",
        "play ", "musical", "performance art", "drag", "burlesque",
        "poetry", "spoken word", "storytelling", "open mic poetry",
        "painting", "sculpture", "photography exhibit", "art walk",
        "comedy night", "funny", "laughs", "laughing",
    ],
    "Sports": [
        "game", " match", "tournament", "race", "5k", "10k", "marathon",
        "triathlon", "sport", "baseball", "football", "soccer", "basketball",
        "hockey", "tennis", "golf", "rock climbing", "obstacle course",
        "mud run", "cycling", "swim meet", "track meet", "wrestling match",
        "boxing", "mma", "fight night", "athletics",
    ],
    "Food": [
        "food", "dining", "tasting", "wine tasting", "beer tasting",
        "craft beer", "restaurant", "cook", "chef", "brunch", "dinner",
        "lunch", "market", "farmers market", "food truck", "cocktail",
        "mixology", "whiskey", "bourbon tasting", "charcuterie", "baking",
        "culinary", "kombucha", "pinot", "merlot", "beer garden",
    ],
    "Family": [
        "family", "kids", "children", "zoo", "aquarium", "carnival",
        "fair ", "parade", "holiday", "easter", "halloween", "christmas",
        "toddler", "youth", "school", "playground", "storytime",
        "puppet", "magic show", "petting zoo",
    ],
}


def guess_category(title, desc="", venue=""):
    text = (title + " " + (desc or "")).lower()
    venue_lower = (venue or "").lower()

    # Venue is a strong signal — check it first for Music/Arts/Food/Family
    for cat, hints in VENUE_HINTS.items():
        if any(h in venue_lower for h in hints):
            venue_cat = cat
            break
    else:
        venue_cat = None

    # Text keyword matching — this takes priority over venue hint
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(k in text for k in keywords):
            return cat

    # If no text match, fall back to venue hint
    return venue_cat or "Other"


def parse_date(raw):
    if not raw:
        return None
    if isinstance(raw, dict):
        raw = raw.get("@value") or raw.get("value") or ""
    raw = str(raw).strip()
    try:
        return dateparser.parse(raw, fuzzy=True).isoformat()
    except Exception:
        return None


def make_id(title, start, url):
    return hashlib.md5(f"{title}|{start}|{url}".encode()).hexdigest()[:12]


def normalize(ld, source, url=None):
    """Convert a schema.org-ish dict to our event schema. Returns None if unusable."""
    title = str(ld.get("name") or ld.get("title") or "").strip()
    if not title:
        return None

    start = parse_date(ld.get("startDate") or ld.get("start"))
    if not start:
        return None

    # Drop past events
    try:
        if datetime.fromisoformat(start) < datetime.now():
            return None
    except Exception:
        pass

    end = parse_date(ld.get("endDate") or ld.get("end"))

    loc = ld.get("location") or ld.get("place")
    venue = loc.get("name") if isinstance(loc, dict) else (loc if isinstance(loc, str) else None)

    desc = re.sub(r"<[^>]+>", " ", str(ld.get("description") or "")).strip()
    desc = re.sub(r"\s+", " ", desc)[:600] or None

    event_url = url or ld.get("url") or ld.get("sameAs")

    schema_type = ld.get("@type", "")
    # Only trust specific schema types — don't blindly accept SportsEvent/SocialEvent
    # since Eventbrite frequently mislabels these
    if schema_type == "MusicEvent":
        category = "Music"
    elif schema_type in ("TheaterEvent", "TheatreEvent"):
        category = "Arts"
    elif schema_type == "FoodEvent":
        category = "Food"
    else:
        category = guess_category(title, desc, venue)

    return {
        "id":          make_id(title, start, event_url or ""),
        "title":       title,
        "start":       start,
        "end":         end,
        "description": desc,
        "url":         event_url,
        "venue":       venue,
        "category":    category,
        "source":      source,
    }


# ── Source scrapers ───────────────────────────────────────────────────────────

def scrape_visit_chattanooga():
    SOURCE = "visitchattanooga.com"
    BASE   = "https://www.visitchattanooga.com"
    events = []

    for page in range(1, 4):
        url = f"{BASE}/events/" if page == 1 else f"{BASE}/events/?page={page}"
        log.info(f"  [{SOURCE}] listing page {page}...")
        s = soup(url)
        if not s:
            break

        links = []
        for a in s.select("a[href*='/events/']"):
            href = a.get("href", "")
            if href and href != "/events/" and href.count("/") >= 3:
                full = href if href.startswith("http") else BASE + href
                if full not in links:
                    links.append(full)

        if not links:
            break

        for i, link in enumerate(links, 1):
            log.info(f"  [{SOURCE}] event {i}/{len(links)}: {link.split('/')[-2]}")
            for item in jsonld(link):
                if item.get("@type") in ("Event", "SocialEvent", "MusicEvent",
                                         "TheaterEvent", "SportsEvent"):
                    ev = normalize(item, SOURCE, link)
                    if ev:
                        events.append(ev)
                        break

        if len(links) < 8:
            break

    log.info(f"  [{SOURCE}] done — {len(events)} events")
    return events


def scrape_chattanooga_pulse():
    SOURCE = "chattanoogapulse.com"
    BASE   = "https://www.chattanoogapulse.com"
    events = []

    for page in range(1, 4):
        url = f"{BASE}/local-events-calendar/" if page == 1 else f"{BASE}/local-events-calendar/?page={page}"
        log.info(f"  [{SOURCE}] page {page}...")
        s = soup(url)
        if not s:
            break

        cards = (s.select("article.event") or s.select(".event-item") or
                 s.select("[class*='event-listing']") or s.select("article"))

        if not cards:
            log.info(f"  [{SOURCE}] no cards found, stopping")
            break

        for card in cards:
            title_el = card.select_one("h2, h3, .event-title, [class*='title']")
            title    = title_el.get_text(strip=True) if title_el else None
            if not title:
                continue

            date_el  = card.select_one("time, [class*='date'], [class*='Date']")
            date_str = (date_el.get("datetime") or date_el.get_text(strip=True)) if date_el else None

            desc_el  = card.select_one("p, .description, [class*='summary']")
            desc     = desc_el.get_text(" ", strip=True)[:500] if desc_el else None

            venue_el = card.select_one("[class*='venue'], [class*='location']")
            venue    = venue_el.get_text(strip=True) if venue_el else None

            link_el  = card.select_one("a[href]")
            link     = link_el.get("href", "") if link_el else None
            if link and not link.startswith("http"):
                link = BASE + link

            ev = normalize({"name": title, "startDate": date_str,
                            "description": desc, "location": {"name": venue} if venue else None},
                           SOURCE, link)
            if ev:
                events.append(ev)

        log.info(f"  [{SOURCE}] page {page}: {len(events)} events so far")
        if len(cards) < 4:
            break

    log.info(f"  [{SOURCE}] done — {len(events)} events")
    return events


def scrape_nooga_today():
    SOURCE = "noogatoday.6amcity.com"
    BASE   = "https://noogatoday.6amcity.com"
    events = []

    for page in range(1, 4):
        url = f"{BASE}/events" if page == 1 else f"{BASE}/events?page={page}"
        log.info(f"  [{SOURCE}] page {page}...")
        r = fetch(url)
        if not r:
            break
        s = BeautifulSoup(r.text, "lxml")

        for item in jsonld(url, html=r.text):
            if item.get("@type") in ("Event", "EventSeries"):
                ev = normalize(item, SOURCE)
                if ev:
                    events.append(ev)

        links = []
        for a in s.select("a[href*='/events/']"):
            href = a.get("href", "")
            if href and href not in ("/events", "/events/"):
                full = href if href.startswith("http") else BASE + href
                if full not in links:
                    links.append(full)

        for i, link in enumerate(links, 1):
            log.info(f"  [{SOURCE}] event {i}/{len(links)}")
            for item in jsonld(link):
                if item.get("@type") in ("Event", "MusicEvent", "SocialEvent"):
                    ev = normalize(item, SOURCE, link)
                    if ev:
                        events.append(ev)
                        break

        log.info(f"  [{SOURCE}] page {page}: {len(events)} events so far")
        if len(links) < 3:
            break

    log.info(f"  [{SOURCE}] done — {len(events)} events")
    return events


def scrape_nooga_nightlife():
    SOURCE = "nooganightlife.com"
    BASE   = "https://www.nooganightlife.com"
    events = []

    log.info(f"  [{SOURCE}] fetching...")
    r = fetch(f"{BASE}/")
    if not r:
        return events

    s = BeautifulSoup(r.text, "lxml")

    for item in jsonld(BASE + "/", html=r.text):
        if item.get("@type") in ("Event", "MusicEvent", "SocialEvent"):
            ev = normalize(item, SOURCE)
            if ev:
                events.append(ev)

    if not events:
        cards = (s.select(".event") or s.select("[class*='event-item']") or
                 s.select("article") or s.select(".show"))
        for card in cards:
            title_el = card.select_one("h2, h3, h4, .title, [class*='title']")
            title    = title_el.get_text(strip=True) if title_el else None
            if not title:
                continue

            date_el  = card.select_one("time, [class*='date'], [class*='when']")
            date_str = (date_el.get("datetime") or date_el.get_text(strip=True)) if date_el else None

            venue_el = card.select_one("[class*='venue'], [class*='location'], [class*='where']")
            venue    = venue_el.get_text(strip=True) if venue_el else None

            desc_el  = card.select_one("p, .description")
            desc     = desc_el.get_text(" ", strip=True)[:400] if desc_el else None

            link_el  = card.select_one("a[href]")
            link     = link_el.get("href", "") if link_el else BASE + "/"
            if link and not link.startswith("http"):
                link = BASE + link

            ev = normalize({"name": title, "startDate": date_str,
                            "description": desc, "location": {"name": venue} if venue else None},
                           SOURCE, link)
            if ev:
                events.append(ev)

    log.info(f"  [{SOURCE}] done — {len(events)} events")
    return events


def scrape_eventbrite():
    SOURCE = "eventbrite.com"
    BASE   = "https://www.eventbrite.com"
    events = []

    for page in range(1, 4):
        url = f"{BASE}/d/tn--chattanooga/events/" if page == 1 else f"{BASE}/d/tn--chattanooga/events/?page={page}"
        log.info(f"  [{SOURCE}] page {page}...")
        s = soup(url)
        if not s:
            break

        links = set()
        for a in s.select("a[href*='/e/']"):
            href = a.get("href", "")
            if "eventbrite.com/e/" in href:
                links.add(href.split("?")[0])

        if not links:
            break

        for i, link in enumerate(links, 1):
            log.info(f"  [{SOURCE}] event {i}/{len(links)}")
            for item in jsonld(link):
                if item.get("@type") in ("Event", "MusicEvent", "SocialEvent",
                                         "TheaterEvent", "SportsEvent",
                                         "FoodEvent", "EducationEvent"):
                    ev = normalize(item, SOURCE, link)
                    if ev:
                        events.append(ev)
                        break

        if len(links) < 5:
            break

    log.info(f"  [{SOURCE}] done — {len(events)} events")
    return events


# ── Orchestrator ──────────────────────────────────────────────────────────────
SOURCES = [
    scrape_visit_chattanooga,
    scrape_chattanooga_pulse,
    scrape_nooga_today,
    scrape_nooga_nightlife,
    scrape_eventbrite,
]


def deduplicate(events):
    seen_ids, seen_slugs, out = set(), set(), []
    for ev in events:
        eid  = ev.get("id", "")
        slug = f"{(ev.get('title') or '').lower().strip()}|{(ev.get('start') or '')[:10]}"
        if eid in seen_ids or slug in seen_slugs:
            continue
        seen_ids.add(eid)
        seen_slugs.add(slug)
        out.append(ev)
    return out


def prune_past(events):
    now = datetime.now()
    out = []
    for ev in events:
        try:
            if datetime.fromisoformat(ev.get("start", "")) >= now:
                out.append(ev)
        except Exception:
            out.append(ev)
    return out


def filter_long_events(events):
    """Drop any event lasting more than 14 days."""
    out = []
    for ev in events:
        start = ev.get("start")
        end   = ev.get("end")
        if start and end:
            try:
                duration = datetime.fromisoformat(end) - datetime.fromisoformat(start)
                if duration.days > 14:
                    log.info(f"  [filter] dropping event >2 weeks ({duration.days}d): {ev['title']}")
                    continue
            except Exception:
                pass
        out.append(ev)
    return out


def main():
    log.info("=== Chattanooga Events Scraper starting ===")

    existing = []
    if JS_FILE.exists():
        try:
            raw = JS_FILE.read_text(encoding="utf-8")
            # Strip the JS wrapper to get the JSON
            json_str = raw.strip().removeprefix("window.CHATTANOOGA_EVENTS =").removesuffix(";").strip()
            payload  = json.loads(json_str)
            existing = payload.get("events", []) if isinstance(payload, dict) else payload
            log.info(f"Loaded {len(existing)} existing events from events.js")
        except Exception as e:
            log.warning(f"Could not read events.js: {e}")

    manual = [ev for ev in existing if ev.get("source") == "manual"]
    log.info(f"Preserving {len(manual)} manual events")

    fresh = []
    for fn in SOURCES:
        try:
            fresh.extend(fn())
        except Exception as e:
            log.error(f"  {fn.__name__} failed: {e}")

    combined = deduplicate(manual + fresh)
    combined = prune_past(combined)
    combined = filter_long_events(combined)
    combined.sort(key=lambda e: e.get("start") or "")

    log.info(f"=== Total: {len(combined)} events after dedup & prune ===")

    payload = {"last_updated": datetime.now(timezone.utc).isoformat(), "events": combined}
    JS_FILE.write_text(
        f"window.CHATTANOOGA_EVENTS = {json.dumps(payload, indent=2, ensure_ascii=False)};\n",
        encoding="utf-8",
    )
    log.info(f"Written to {JS_FILE}")


if __name__ == "__main__":
    main()
