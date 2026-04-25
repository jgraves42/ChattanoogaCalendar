"""
Base scraper class and shared helpers used by all source scrapers.
"""
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone

import extruct
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

CATEGORY_KEYWORDS = {
    "Music":  ["concert", "music", "band", "live music", "jazz", "bluegrass",
                "orchestra", "symphony", "dj", "festival", "singer"],
    "Arts":   ["art", "gallery", "exhibit", "theatre", "theater", "dance",
                "ballet", "opera", "film", "movie", "comedy", "improv", "play"],
    "Sports": ["game", "match", "tournament", "race", "5k", "marathon",
                "triathlon", "sport", "baseball", "football", "soccer", "basketball",
                "hockey", "tennis", "golf", "climbing"],
    "Food":   ["food", "dining", "tasting", "wine", "beer", "craft beer",
                "restaurant", "cook", "chef", "brunch", "market", "farmers market"],
    "Family": ["family", "kids", "children", "zoo", "aquarium", "carnival",
                "fair", "parade", "holiday", "easter", "halloween", "christmas"],
}


def guess_category(title: str, desc: str) -> str:
    combined = (title + " " + (desc or "")).lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in combined for kw in keywords):
            return category
    return "Other"


def parse_date(raw) -> str | None:
    """Return ISO-8601 string or None."""
    if not raw:
        return None
    if isinstance(raw, dict):
        raw = raw.get("@value") or raw.get("value") or ""
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        dt = dateparser.parse(raw, fuzzy=True)
        return dt.isoformat() if dt else None
    except Exception:
        return None


def make_id(title: str, start: str, url: str) -> str:
    key = f"{title}|{start}|{url}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def normalize_event(ld: dict, source: str, url: str | None = None) -> dict | None:
    """Convert a schema.org Event dict (or partial HTML-parsed dict) to our schema."""
    title = ld.get("name") or ld.get("title")
    if not title:
        return None

    title = str(title).strip()
    start = parse_date(ld.get("startDate") or ld.get("start"))

    # Skip events without a parseable start date
    if not start:
        return None

    # Skip events that have already passed
    try:
        if datetime.fromisoformat(start) < datetime.now():
            return None
    except Exception:
        pass

    end = parse_date(ld.get("endDate") or ld.get("end"))

    # Location
    location = ld.get("location") or ld.get("place")
    venue = None
    if isinstance(location, dict):
        venue = location.get("name")
    elif isinstance(location, str):
        venue = location

    # Description
    desc = ld.get("description") or ""
    desc = re.sub(r"<[^>]+>", " ", str(desc)).strip()
    desc = re.sub(r"\s+", " ", desc)[:600]

    # URL
    event_url = url or ld.get("url") or ld.get("sameAs")

    # Category — use schema @type hint first, then keyword matching
    schema_type = ld.get("@type", "")
    if "Music" in schema_type:
        category = "Music"
    elif "Theater" in schema_type or "Theatre" in schema_type:
        category = "Arts"
    elif "Sports" in schema_type:
        category = "Sports"
    elif "Food" in schema_type:
        category = "Food"
    else:
        category = guess_category(title, desc)

    return {
        "id": make_id(title, start, event_url or ""),
        "title": title,
        "start": start,
        "end": end,
        "description": desc or None,
        "url": event_url,
        "venue": venue,
        "category": category,
        "source": source,
    }


class BaseScraper:
    source_name: str = "unknown"

    def __init__(self, session: requests.Session | None = None, delay: float = 1.0):
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)
        self.delay = delay

    def get(self, url: str) -> requests.Response | None:
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            time.sleep(self.delay)
            return resp
        except Exception as e:
            logger.warning(f"[{self.source_name}] GET failed {url}: {e}")
            return None

    def get_soup(self, url: str) -> BeautifulSoup | None:
        resp = self.get(url)
        if resp is None:
            return None
        return BeautifulSoup(resp.text, "lxml")

    def extract_jsonld(self, url: str, soup: BeautifulSoup | None = None) -> list[dict]:
        """Return a flat list of JSON-LD items from a page."""
        if soup is None:
            resp = self.get(url)
            if resp is None:
                return []
            html = resp.text
        else:
            html = str(soup)

        try:
            data = extruct.extract(html, base_url=url, syntaxes=["json-ld"])
            items = data.get("json-ld", [])
            # Flatten @graph arrays
            flat = []
            for item in items:
                if "@graph" in item:
                    flat.extend(item["@graph"])
                else:
                    flat.append(item)
            return flat
        except Exception as e:
            logger.debug(f"[{self.source_name}] extruct failed {url}: {e}")
            return []

    def scrape(self) -> list[dict]:
        raise NotImplementedError
