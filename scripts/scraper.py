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


def scrape_chatt_zoo():
    """
    chattzoo.org uses a custom page builder layout — not The Events Calendar plugin.
    The listing page (/events/) shows event titles in <h3> tags with "Learn More"
    links pointing to /events/<slug>/. We collect those links, then visit each
    detail page where JSON-LD and structured content are more likely to exist.
    """
    SOURCE = "chattzoo.org"
    BASE   = "https://www.chattzoo.org"
    events = []

    DAILY_SKIP = {
        "feeding", "keeper talk", "keeper chat", "daily", "every day",
        "animal encounter", "am feeding", "pm feeding",
        "morning feeding", "afternoon feeding",
    }

    def is_daily_schedule(title, desc=""):
        text = (title + " " + (desc or "")).lower()
        return any(k in text for k in DAILY_SKIP)

    # Step 1: collect event detail page links from the listing page
    log.info(f"  [{SOURCE}] fetching event listing...")
    r = fetch(f"{BASE}/events/")
    if not r:
        return events

    s = BeautifulSoup(r.text, "lxml")

    # Collect all links that match /events/<slug> (not just /events/ itself)
    links = []
    for a in s.select("a[href*='/events/']"):
        href = a.get("href", "")
        # Must be a specific event slug, not the listing page or pagination
        if (href and
                "/events/" in href and
                not href.rstrip("/").endswith("/events") and
                "page" not in href and
                href not in links):
            full = href if href.startswith("http") else BASE + href
            links.append(full)

    log.info(f"  [{SOURCE}] found {len(links)} event links")

    # Step 2: visit each detail page
    for i, link in enumerate(links, 1):
        log.info(f"  [{SOURCE}] event {i}/{len(links)}: {link.split('/')[-2]}")

        # Try JSON-LD on the detail page first
        detail_ld = jsonld(link)
        scraped = False
        for item in detail_ld:
            if item.get("@type") in ("Event", "SocialEvent", "FoodEvent"):
                title = str(item.get("name") or "").strip()
                if title and not is_daily_schedule(title, item.get("description", "")):
                    ev = normalize(item, SOURCE, link)
                    if ev:
                        events.append(ev)
                        scraped = True
                        break

        if scraped:
            continue

        # HTML fallback on the detail page
        detail_r = fetch(link)
        if not detail_r:
            continue
        ds = BeautifulSoup(detail_r.text, "lxml")

        # Title: h1 is most reliable on a detail page
        title_el = ds.select_one("h1, h2, .entry-title")
        title = title_el.get_text(strip=True) if title_el else None
        if not title or is_daily_schedule(title):
            continue

        # chattzoo.org puts date+time in <h5> as "May 16 | 6:00 PM - 9:00 PM"
        # Fall back to <time> or class-based selectors for other pages
        date_str = None
        h5_el = ds.select_one("h5")
        if h5_el:
            h5_text = h5_el.get_text(strip=True)
            # Format: "May 16 | 6:00 PM - 9:00 PM"
            if "|" in h5_text:
                parts = h5_text.split("|")
                date_part = parts[0].strip()
                time_part = parts[1].strip().split("-")[0].strip() if len(parts) > 1 else ""
                current_year = datetime.now().year
                date_str = f"{date_part} {current_year} {time_part}".strip()
            else:
                date_str = h5_text

        if not date_str:
            date_el = ds.select_one("time, [class*='date'], [class*='Date'], [class*='when']")
            date_str = (date_el.get("datetime") or date_el.get_text(strip=True)) if date_el else None

        # Description: first substantial paragraph
        desc_el = ds.select_one(".entry-content p, article p, .post-content p, main p")
        desc = desc_el.get_text(" ", strip=True)[:500] if desc_el else None

        ev = normalize({
            "name": title, "startDate": date_str, "description": desc,
            "location": {"name": "Chattanooga Zoo"},
        }, SOURCE, link)
        if ev:
            events.append(ev)

    log.info(f"  [{SOURCE}] done — {len(events)} events")
    return events


def scrape_tn_aquarium():
    """
    tnaqua.org uses a custom WordPress theme — no Events Calendar plugin.
    Events are structured as:
        <a href="[ticket or detail URL]">
          <figure class="wp-block-image">...</figure>
          <h4>Event Title</h4>
          <h6>May 5</h6>   ← date without year
        </a>
    We scrape /events-programs/ which has more listings than /calendar/.
    """
    SOURCE = "tnaqua.org"
    BASE   = "https://tnaqua.org"
    events = []

    for url in [f"{BASE}/events-programs/", f"{BASE}/calendar/"]:
        log.info(f"  [{SOURCE}] fetching {url}...")
        r = fetch(url)
        if not r:
            continue

        s = BeautifulSoup(r.text, "lxml")

        # Each event is an <a> tag containing an <h4> (title) and <h6> (date)
        for a in s.select("a"):
            title_el = a.select_one("h4, h3, h2")
            date_el  = a.select_one("h6, h5")
            if not title_el or not date_el:
                continue

            title    = title_el.get_text(strip=True)
            date_raw = date_el.get_text(strip=True)
            link     = a.get("href", BASE)

            if not title or not date_raw:
                continue

            # Date comes without a year ("May 5") — append current year
            current_year = datetime.now().year
            date_str = f"{date_raw} {current_year}"

            # If parsed date is in the past, try next year
            try:
                parsed = dateparser.parse(date_str, fuzzy=True)
                if parsed and parsed < datetime.now():
                    date_str = f"{date_raw} {current_year + 1}"
            except Exception:
                pass

            # Use the tnaqua detail page as the URL when available,
            # falling back to the ticket URL
            if link and not link.startswith("http"):
                link = BASE + link

            ev = normalize({
                "name": title, "startDate": date_str,
                "location": {"name": "Tennessee Aquarium"},
            }, SOURCE, link)
            if ev:
                events.append(ev)

    log.info(f"  [{SOURCE}] done — {len(events)} events")
    return events


def scrape_chatt_ren_faire():
    """
    chattrenfaire.com is a simple WordPress homepage for one annual event —
    no /events/ path exists. The main faire date and sub-events (King & Queen Feast)
    are listed directly on the homepage and inner pages.
    We try JSON-LD first, then scan page text for date patterns.
    """
    SOURCE = "chattrenfaire.com"
    BASE   = "https://chattrenfaire.com"
    events = []

    # Pages known to contain event info
    pages = [
        (BASE + "/",                "Chattanooga Renaissance Faire", "Coolidge Park, 150 River St, Chattanooga, TN"),
        (BASE + "/king-queen-feast/", "King & Queen Feast",          "Coolidge Park, Chattanooga, TN"),
    ]

    # Regex to find date strings like "Saturday, May 30, 2026" or "May 30, 2026"
    DATE_RE = re.compile(
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s*"
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2},?\s+\d{4}",
        re.IGNORECASE,
    )
    TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*(?:AM|PM)", re.IGNORECASE)

    for url, fallback_title, fallback_venue in pages:
        log.info(f"  [{SOURCE}] fetching {url}...")
        r = fetch(url)
        if not r:
            continue

        # Try JSON-LD first
        scraped = False
        for item in jsonld(url, html=r.text):
            if item.get("@type") in ("Event", "SocialEvent", "Festival"):
                ev = normalize(item, SOURCE, url)
                if ev:
                    events.append(ev)
                    scraped = True

        if scraped:
            continue

        # Extract visible text and hunt for date patterns
        s = BeautifulSoup(r.text, "lxml")
        full_text = s.get_text(" ")

        date_match = DATE_RE.search(full_text)
        time_matches = TIME_RE.findall(full_text)
        date_str = date_match.group(0) if date_match else None

        # Build a datetime string combining date + start time if found
        if date_str and time_matches:
            date_str = f"{date_str} {time_matches[0]}"

        # Description: first substantial paragraph
        desc_el = s.select_one(".entry-content p, article p, main p, p")
        desc = desc_el.get_text(" ", strip=True)[:500] if desc_el else None

        # Page title as event title
        title_el = s.select_one("h1, .entry-title")
        title = title_el.get_text(strip=True) if title_el else fallback_title

        ev = normalize({
            "name": title or fallback_title,
            "startDate": date_str,
            "description": desc,
            "location": {"name": fallback_venue},
        }, SOURCE, url)
        if ev:
            events.append(ev)

    log.info(f"  [{SOURCE}] done — {len(events)} events")
    return events


def scrape_lookouts():
    """
    Chattanooga Lookouts (Double-A, team ID 467) home game schedule.
    Uses the public MLB Stats API — milb.com blocks scrapers with 406.
    Only home games are included since those are relevant to local attendees.
    """
    SOURCE  = "milb.com/chattanooga"
    TEAM_ID = 467   # Chattanooga Lookouts
    VENUE   = "AT&T Field, Chattanooga"
    URL     = "https://milb.com/chattanooga"
    events  = []

    season = datetime.now().year
    api_url = (
        f"https://statsapi.mlb.com/api/v1/schedule"
        f"?teamId={TEAM_ID}&sportId=12&season={season}"
        f"&startDate={season}-01-01&endDate={season}-12-31"
        f"&gameType=R&hydrate=team,venue,game(content(summary))"
    )
    log.info(f"  [{SOURCE}] fetching schedule via MLB Stats API...")
    r = fetch(api_url)
    if not r:
        log.warning(f"  [{SOURCE}] API request failed")
        return events

    try:
        data = r.json()
    except Exception as e:
        log.warning(f"  [{SOURCE}] JSON parse failed: {e}")
        return events

    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            # Only include home games
            home_team = game.get("teams", {}).get("home", {}).get("team", {})
            if home_team.get("id") != TEAM_ID:
                continue

            away_team  = game.get("teams", {}).get("away", {}).get("team", {})
            away_name  = away_team.get("name", "Opponent")
            game_date  = game.get("gameDate")     # ISO 8601 with time UTC
            game_id    = str(game.get("gamePk", ""))
            status     = game.get("status", {}).get("abstractGameState", "")

            if not game_date:
                continue

            title = f"Chattanooga Lookouts vs {away_name}"
            desc  = f"Chattanooga Lookouts home game at AT&T Field. Status: {status}."

            ev = normalize({
                "name":        title,
                "startDate":   game_date,
                "description": desc,
                "location":    {"name": VENUE},
            }, SOURCE, URL)

            if ev:
                ev["id"] = f"lookouts-{game_id}"   # stable ID from game PK
                ev["category"] = "Sports"
                events.append(ev)

    log.info(f"  [{SOURCE}] done — {len(events)} home games")
    return events


# ── Orchestrator ──────────────────────────────────────────────────────────────
SOURCES = [
    scrape_visit_chattanooga,
    scrape_chattanooga_pulse,
    scrape_nooga_today,
    scrape_nooga_nightlife,
    scrape_chatt_zoo,
    scrape_tn_aquarium,
    scrape_chatt_ren_faire,
    scrape_lookouts,
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
