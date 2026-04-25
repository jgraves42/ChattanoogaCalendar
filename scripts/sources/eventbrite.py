"""
Scraper for Eventbrite Chattanooga events.
Eventbrite embeds rich JSON-LD on event pages; we use their search listing
to collect links, then extract JSON-LD from each event page.
"""
import logging
from .base import BaseScraper, normalize_event

logger = logging.getLogger(__name__)

LISTING_URL = "https://www.eventbrite.com/d/tn--chattanooga/events/"
BASE_URL = "https://www.eventbrite.com"
MAX_PAGES = 5


class EventbriteScraper(BaseScraper):
    source_name = "eventbrite.com"

    def scrape(self):
        events = []
        for page_num in range(1, MAX_PAGES + 1):
            url = LISTING_URL if page_num == 1 else f"{LISTING_URL}?page={page_num}"
            soup = self.get_soup(url)
            if soup is None:
                break

            # Collect event detail links
            links = set()
            for a in soup.select("a[href*='/e/']"):
                href = a.get("href", "")
                if href and "eventbrite.com/e/" in href:
                    # Strip query params
                    clean = href.split("?")[0]
                    links.add(clean)

            if not links:
                break

            for link in links:
                ld_items = self.extract_jsonld(link)
                for item in ld_items:
                    if item.get("@type") in ("Event", "MusicEvent", "SocialEvent",
                                              "TheaterEvent", "SportsEvent",
                                              "FoodEvent", "EducationEvent"):
                        ev = normalize_event(item, source=self.source_name, url=link)
                        if ev:
                            events.append(ev)
                            break

            if len(links) < 5:
                break

        logger.info(f"[eventbrite] Found {len(events)} events")
        return events
