"""
Scraper for chattanoogapulse.com/local-events-calendar
Uses BeautifulSoup HTML parsing — the site does not consistently publish JSON-LD.
"""
import logging
from .base import BaseScraper, normalize_event

logger = logging.getLogger(__name__)

LISTING_URL = "https://www.chattanoogapulse.com/local-events-calendar/"
MAX_PAGES = 8


class ChattanoogaPulseScraper(BaseScraper):
    source_name = "chattanoogapulse.com"

    def scrape(self):
        events = []
        for page_num in range(1, MAX_PAGES + 1):
            url = LISTING_URL if page_num == 1 else f"{LISTING_URL}?page={page_num}"
            soup = self.get_soup(url)
            if soup is None:
                break

            # Each event is typically in an article or .event-* block
            cards = (soup.select("article.event") or
                     soup.select(".event-item") or
                     soup.select("[class*='event-listing']") or
                     soup.select("article"))

            if not cards:
                break

            for card in cards:
                ev = self._parse_card(card)
                if ev:
                    events.append(ev)

            if len(cards) < 4:
                break

        logger.info(f"[chattanoogapulse] Found {len(events)} events")
        return events

    def _parse_card(self, card):
        title_el = card.select_one("h2, h3, .event-title, [class*='title']")
        title = title_el.get_text(strip=True) if title_el else None
        if not title:
            return None

        date_el = card.select_one("time, [class*='date'], [class*='Date']")
        date_str = (date_el.get("datetime") or date_el.get_text(strip=True)) if date_el else None

        desc_el = card.select_one("p, .description, [class*='summary']")
        desc = desc_el.get_text(" ", strip=True)[:500] if desc_el else None

        venue_el = card.select_one("[class*='venue'], [class*='location']")
        venue = venue_el.get_text(strip=True) if venue_el else None

        link_el = card.select_one("a[href]")
        url = link_el.get("href", "") if link_el else None
        if url and not url.startswith("http"):
            url = "https://www.chattanoogapulse.com" + url

        return normalize_event({
            "name": title,
            "startDate": date_str,
            "description": desc,
            "location": {"name": venue} if venue else None,
        }, source=self.source_name, url=url)
