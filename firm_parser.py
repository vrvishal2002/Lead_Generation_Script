import re
import time
import random
import pandas as pd
from urllib.parse import urlparse, urljoin, quote_plus
from bs4 import BeautifulSoup
from soup_content_lib import selenium_chrome_driver
from log_lib import log


def _parse_query(query):
    """Split 'medical malpractice lawyers in Connecticut' into (terms, location)."""
    match = re.search(r'\bin\s+(.+)$', query, re.IGNORECASE)
    if match:
        location = match.group(1).strip()
        terms = query[:match.start()].strip()
    else:
        parts = query.rsplit(' ', 1)
        terms, location = (parts[0], parts[1]) if len(parts) == 2 else (query, "")
    # Normalise lawyer/lawyers/attorney/attorneys → attorneys for YP search
    terms = re.sub(r'\blawyers?\b|\battorneys?\b', 'attorneys', terms, flags=re.IGNORECASE).strip()
    terms = re.sub(r'\s+', '-', terms).lower()
    return terms, location


class FirmParser:

    def __init__(self, log_path=None):
        self.log_path = log_path

    def _extract_page(self, driver):
        soup = BeautifulSoup(driver.page_source, "html.parser")
        firms = []
        for card in soup.select(".result"):
            name_tag = card.select_one("a.business-name span")
            website_tag = card.select_one("a.track-visit-website")
            if not name_tag or not website_tag:
                continue
            name = name_tag.get_text(strip=True)
            raw = website_tag.get("href", "")
            if not raw or not raw.startswith("http"):
                continue
            parsed = urlparse(raw)
            website = f"{parsed.scheme}://{parsed.netloc}"
            if name and website:
                firms.append({"Firm Name": name, "Website": website})
        return firms

    def scrape_google_places(self, query, target=50, status_callback=None, cancel_event=None):
        """Search Yellow Pages for firms matching the query.
        Returns list of {Firm Name, Website}."""
        if cancel_event and cancel_event.is_set():
            return []

        terms, location = _parse_query(query)
        base_url = (
            f"https://www.yellowpages.com/{quote_plus(location).replace('%20', '-').lower()}"
            f"/{terms}"
        )
        log(f"Fetching: {base_url}", self.log_path)

        results = []
        seen_websites = set()
        page_num = 1

        try:
            with selenium_chrome_driver() as driver:
                while len(results) < target:
                    if cancel_event and cancel_event.is_set():
                        break

                    url = base_url if page_num == 1 else (
                        f"https://www.yellowpages.com/search"
                        f"?search_terms={terms.replace('-', '+')}"
                        f"&geo_location_terms={quote_plus(location)}"
                        f"&page={page_num}"
                    )

                    driver.get(url)
                    time.sleep(random.uniform(3.0, 5.0))

                    firms = self._extract_page(driver)
                    if not firms:
                        log(f"No listings on page {page_num} — stopping.", self.log_path)
                        break

                    log(f"Page {page_num}: {len(firms)} listings found.", self.log_path)

                    for firm in firms:
                        if cancel_event and cancel_event.is_set():
                            break
                        if firm["Website"] in seen_websites:
                            continue
                        seen_websites.add(firm["Website"])
                        results.append(firm)
                        if status_callback:
                            status_callback(len(results), target)
                        log(f"{len(results)}. {firm['Firm Name']} — {firm['Website']}", self.log_path)
                        if len(results) >= target:
                            break

                    # Check if a next page exists
                    soup = BeautifulSoup(driver.page_source, "html.parser")
                    if not soup.select_one("a.next"):
                        log("No more pages.", self.log_path)
                        break

                    page_num += 1
                    time.sleep(random.uniform(1.5, 3.0))

        except Exception as e:
            log(f"Error while executing '{query}': {e}", self.log_path)

        return results


if __name__ == "__main__":

    query = "medical malpractice lawyers in Connecticut"
    target = 100

    data = FirmParser().scrape_google_places(query, target)

    df = pd.DataFrame(data)
    df.to_csv("google_places_firms.csv", index=False)
    print(f"\nSaved {len(data)} firms to google_places_firms.csv")
