"""
Scraper for nooganightlife.com
Site organises events by day-of-week tabs; scrape the main listings page.
"""
import logging
from .base import BaseScraper, normalize_event

logger = logging.getLogger(__name__)

LISTING_URL = "https://www.nooganightlife.com/"


class NoogaNightlifeScraper(BaseScraper):
    source_name = "nooganightlife.com"

    def scrape(self):
        events = []
        soup = self.get_soup(LISTING_URL)
        if soup is None:
            return events

        # Try JSON-LD on the page
        for item in self.extract_jsonld(LISTING_URL, soup=soup):
            if item.get("@type") in ("Event", "MusicEvent", "SocialEvent"):
                ev = normalize_event(item, source=self.source_name)
                if ev:
                    events.append(ev)

        if events:
            logger.info(f"[nooganightlife] Found {len(events)} events via JSON-LD")
            return events

        # HTML fallback — events listed as .event or similar blocks
        cards = (soup.select(".event") or
                 soup.select("[class*='event-item']") or
                 soup.select("article") or
                 soup.select(".show"))

        for card in cards:
            title_el = card.select_one("h2, h3, h4, .title, [class*='title']")
            title = title_el.get_text(strip=True) if title_el else None
            if not title:
                continue

            date_el = card.select_one("time, [class*='date'], [class*='when']")
            date_str = (date_el.get("datetime") or date_el.get_text(strip=True)) if date_el else None

            venue_el = card.select_one("[class*='venue'], [class*='location'], [class*='where']")
            venue = venue_el.get_text(strip=True) if venue_el else None

            desc_el = card.select_one("p, .description")
            desc = desc_el.get_text(" ", strip=True)[:400] if desc_el else None

            link_el = card.select_one("a[href]")
            url = link_el.get("href", "") if link_el else LISTING_URL
            if url and not url.startswith("http"):
                url = "https://www.nooganightlife.com" + url

            ev = normalize_event({
                "name": title,
                "startDate": date_str,
                "description": desc,
                "location": {"name": venue} if venue else None,
            }, source=self.source_name, url=url)
            if ev:
                events.append(ev)

        logger.info(f"[nooganightlife] Found {len(events)} events")
        return events
