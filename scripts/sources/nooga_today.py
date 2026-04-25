"""
Scraper for noogatoday.6amcity.com/events
6am City sites often embed JSON-LD; fallback to HTML parsing.
"""
import logging
from .base import BaseScraper, normalize_event

logger = logging.getLogger(__name__)

BASE_URL = "https://noogatoday.6amcity.com"
LISTING_URL = "https://noogatoday.6amcity.com/events"
MAX_PAGES = 6


class NoogaTodayScraper(BaseScraper):
    source_name = "noogatoday.6amcity.com"

    def scrape(self):
        events = []
        for page_num in range(1, MAX_PAGES + 1):
            url = LISTING_URL if page_num == 1 else f"{LISTING_URL}?page={page_num}"
            soup = self.get_soup(url)
            if soup is None:
                break

            # Try JSON-LD on the listing page first
            for item in self.extract_jsonld(url, soup=soup):
                if item.get("@type") in ("Event", "EventSeries"):
                    ev = normalize_event(item, source=self.source_name)
                    if ev:
                        events.append(ev)

            # Also collect card links for detail-page scraping
            links = []
            for a in soup.select("a[href*='/events/']"):
                href = a.get("href", "")
                if href and href != "/events" and href != "/events/":
                    full = href if href.startswith("http") else BASE_URL + href
                    if full not in links:
                        links.append(full)

            for link in links:
                ld = self.extract_jsonld(link)
                scraped = False
                for item in ld:
                    if item.get("@type") in ("Event", "MusicEvent", "SocialEvent"):
                        ev = normalize_event(item, source=self.source_name, url=link)
                        if ev:
                            events.append(ev)
                            scraped = True
                            break
                if not scraped:
                    ev = self._parse_detail_page(link)
                    if ev:
                        events.append(ev)

            if len(links) < 3:
                break

        logger.info(f"[noogatoday] Found {len(events)} events")
        return events

    def _parse_detail_page(self, url):
        soup = self.get_soup(url)
        if soup is None:
            return None
        title_el = soup.select_one("h1")
        title = title_el.get_text(strip=True) if title_el else None
        if not title:
            return None
        date_el = soup.select_one("time, [class*='date']")
        date_str = (date_el.get("datetime") or date_el.get_text(strip=True)) if date_el else None
        desc_el = soup.select_one("article p, .content p, .body p")
        desc = desc_el.get_text(" ", strip=True)[:500] if desc_el else None
        return normalize_event({
            "name": title,
            "startDate": date_str,
            "description": desc,
        }, source=self.source_name, url=url)
