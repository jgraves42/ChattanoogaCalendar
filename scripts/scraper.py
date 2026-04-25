#!/usr/bin/env python3
"""
Chattanooga Events Calendar — main scraper orchestrator.

Run:
    python scripts/scraper.py

Reads:  data/events.json  (existing events, preserves manual entries)
Writes: data/events.json  (merged, deduplicated, sorted output)
"""
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Path setup ────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).parent.parent
DATA_FILE   = REPO_ROOT / "data" / "events.json"

sys.path.insert(0, str(Path(__file__).parent))

from sources.visit_chattanooga  import VisitChattanoogaScraper
from sources.chattanooga_pulse  import ChattanoogaPulseScraper
from sources.nooga_today        import NoogaTodayScraper
from sources.nooga_nightlife    import NoogaNightlifeScraper
from sources.eventbrite         import EventbriteScraper

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

SCRAPERS = [
    VisitChattanoogaScraper,
    ChattanoogaPulseScraper,
    NoogaTodayScraper,
    NoogaNightlifeScraper,
    EventbriteScraper,
]


# ── Load existing events ──────────────────────────────────────────────────────
def load_existing() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    try:
        payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload.get("events", [])
        if isinstance(payload, list):
            return payload
    except Exception as e:
        logger.warning(f"Could not read existing events.json: {e}")
    return []


# ── Deduplication ─────────────────────────────────────────────────────────────
def deduplicate(events: list[dict]) -> list[dict]:
    seen_ids = set()
    seen_slugs = set()   # title+date slug for fuzzy dedup
    out = []
    for ev in events:
        eid = ev.get("id", "")
        slug = f"{(ev.get('title') or '').lower().strip()}|{(ev.get('start') or '')[:10]}"

        if eid in seen_ids or slug in seen_slugs:
            continue
        seen_ids.add(eid)
        seen_slugs.add(slug)
        out.append(ev)
    return out


# ── Prune past events ─────────────────────────────────────────────────────────
def prune_past(events: list[dict]) -> list[dict]:
    now = datetime.now()
    kept = []
    for ev in events:
        start = ev.get("start")
        if not start:
            kept.append(ev)
            continue
        try:
            if datetime.fromisoformat(start) >= now:
                kept.append(ev)
        except Exception:
            kept.append(ev)
    return kept


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    logger.info("Starting Chattanooga Events scraper")

    # 1. Load existing events (manual entries use source="manual")
    existing = load_existing()
    manual   = [ev for ev in existing if ev.get("source") == "manual"]
    logger.info(f"  Loaded {len(existing)} existing events ({len(manual)} manual)")

    # 2. Run all scrapers
    session = requests.Session()
    fresh: list[dict] = []

    for ScraperClass in SCRAPERS:
        name = ScraperClass.source_name
        try:
            scraper = ScraperClass(session=session, delay=0.8)
            results = scraper.scrape()
            logger.info(f"  {name}: {len(results)} events scraped")
            fresh.extend(results)
        except Exception as e:
            logger.error(f"  {name}: scraper failed — {e}")

    # 3. Merge: manual + freshly scraped
    combined = manual + fresh

    # 4. Deduplicate & prune
    combined = deduplicate(combined)
    combined = prune_past(combined)

    # 5. Sort ascending by start date
    combined.sort(key=lambda e: e.get("start") or "")

    logger.info(f"  Total after merge+dedup+prune: {len(combined)} events")

    # 6. Write output
    payload = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "events": combined,
    }
    DATA_FILE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(f"  Written to {DATA_FILE}")


if __name__ == "__main__":
    main()
