"""
Scraper for visitchattanooga.com/events
Uses JSON-LD structured data (schema.org/Event) embedded in each event page,
plus BeautifulSoup to collect event page links from the listing pages.
"""
import logging
from .base import BaseScraper, normalize_event

logger = logging.getLogger(__name__)

BASE_URL = "https://www.visitchattanooga.com"
LISTING_URL = "https://www.visitchattanooga.com/events/"

# Max listing pages to walk (each page has ~12 events)
MAX_PAGES = 10


class VisitChattanoogaScraper(BaseScraper):
    source_name = "visitchattanooga.com"

    def scrape(self):
        events = []
        for page_num in range(1, MAX_PAGES + 1):
            url = LISTING_URL if page_num == 1 else f"{LISTING_URL}?page={page_num}"
            soup = self.get_soup(url)
            if soup is None:
                break

            # Collect event detail links
            links = []
            for a in soup.select("a[href*='/events/']"):
                href = a.get("href", "")
                # Filter out the listing page itself and pagination links
                if href and href != "/events/" and href.count("/") >= 3:
                    full = href if href.startswith("http") else BASE_URL + href
                    if full not in links:
                        links.append(full)

            if not links:
                break

            for link in links:
                ev = self._scrape_event_page(link)
                if ev:
                    events.append(ev)

            # Stop early if the last page has fewer results than expected
            if len(links) < 8:
                break

        logger.info(f"[visitchattanooga] Found {len(events)} events")
        return events

    def _scrape_event_page(self, url):
        # Try JSON-LD first
        ld = self.extract_jsonld(url)
        for item in ld:
            if item.get("@type") in ("Event", "SocialEvent", "MusicEvent",
                                      "TheaterEvent", "SportsEvent"):
                return normalize_event(item, source=self.source_name, url=url)

        # Fallback: parse HTML directly
        soup = self.get_soup(url)
        if soup is None:
            return None

        title_el = soup.select_one("h1")
        title = title_el.get_text(strip=True) if title_el else None
        if not title:
            return None

        date_el = soup.select_one("[class*='date'], [class*='Date'], time")
        date_str = date_el.get_text(strip=True) if date_el else None

        desc_el = soup.select_one("[class*='description'], [class*='body'], article p")
        desc = desc_el.get_text(" ", strip=True)[:500] if desc_el else None

        venue_el = soup.select_one("[class*='venue'], [class*='location']")
        venue = venue_el.get_text(strip=True) if venue_el else None

        return normalize_event({
            "name": title,
            "startDate": date_str,
            "description": desc,
            "location": {"name": venue} if venue else None,
        }, source=self.source_name, url=url)
