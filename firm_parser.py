from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
import pandas as pd
import time
import random
from soup_content_lib import selenium_chrome_driver, human_scroll_to_bottom
from urllib.parse import urlparse, urljoin
from log_lib import log


class FirmParser:

    def __init__(self, log_path=None):
        self.log_path = log_path

    def _is_captcha_page(self, driver):
        src = driver.page_source.lower()
        return any(k in src for k in ["captcha", "not a robot", "unusual traffic", "recaptcha", "verify you're human"])

    def _human_click(self, driver, element):
        """Scroll element into view, hover, then click — no JS click."""
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element
            )
            time.sleep(random.uniform(0.3, 0.6))
            ActionChains(driver) \
                .move_to_element(element) \
                .pause(random.uniform(0.2, 0.5)) \
                .click() \
                .perform()
        except Exception:
            # Fallback if ActionChains fails on this node
            driver.execute_script("arguments[0].click();", element)

    def scrape_google_places(self, query, target=50, status_callback=None, cancel_event=None):
        if cancel_event and cancel_event.is_set():
            return []

        try:
            with selenium_chrome_driver() as driver:
                # selenium_chrome_driver() already warmed up on google.com
                url = f"https://www.google.com/search?q={query}&tbm=lcl"
                log(f"Fetching: {url}", self.log_path)
                driver.get(url)
                time.sleep(random.uniform(2.0, 4.0))

                if cancel_event and cancel_event.is_set():
                    return []

                if self._is_captcha_page(driver):
                    log("CAPTCHA detected — aborting this query", self.log_path)
                    return []

                wait = WebDriverWait(driver, 20)
                results = []
                seen_names = set()
                visited_websites = set()
                page = 1

                while len(results) < target:
                    if cancel_event and cancel_event.is_set():
                        break

                    if self._is_captcha_page(driver):
                        log(f"CAPTCHA appeared mid-session on page {page} — stopping", self.log_path)
                        break

                    # Human-paced inter-page delay
                    for _ in range(int(random.uniform(6, 12))):
                        if cancel_event and cancel_event.is_set():
                            break
                        time.sleep(1)

                    log(f"\nScraping page {page}...", self.log_path)

                    # Scroll down like a human reading the page
                    human_scroll_to_bottom(driver, cancel_event)

                    try:
                        wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, "VkpGBb")))
                    except Exception:
                        log(f"No listings found on page {page}.", self.log_path)
                        break

                    listings = driver.find_elements(By.CLASS_NAME, "VkpGBb")
                    log(f"Found {len(listings)} listings.", self.log_path)

                    for item in listings:
                        if cancel_event and cancel_event.is_set():
                            break
                        try:
                            name = item.text.split("\n")[0].strip()
                            if name in seen_names or "Sponsored" in name:
                                continue
                            seen_names.add(name)

                            # Human-like click (hover → pause → click)
                            self._human_click(driver, item)
                            time.sleep(random.uniform(1.5, 2.5))

                            website = None
                            try:
                                anchors = item.find_elements(By.TAG_NAME, "a")
                                for a in anchors:
                                    if "website" in a.text.strip().lower():
                                        raw_href = a.get_attribute("href")
                                        if raw_href:
                                            if raw_href.startswith("/"):
                                                raw_href = urljoin("https://www.google.com", raw_href)
                                            parsed = urlparse(raw_href)
                                            if parsed and parsed.netloc:
                                                website = f"{parsed.scheme or 'https'}://{parsed.netloc}"
                                        break
                            except Exception:
                                website = None

                            if website and website not in visited_websites:
                                visited_websites.add(website)
                                results.append({"Firm Name": name, "Website": website})
                                if status_callback:
                                    status_callback(len(results), target)
                                log(f"{len(results)}. Collected: {name}", self.log_path)
                            else:
                                log(f"No website for {name}", self.log_path)

                            if len(results) >= target:
                                break

                            # Small random pause between listings to avoid machine-gun clicking
                            time.sleep(random.uniform(0.4, 1.0))

                        except Exception:
                            continue

                    try:
                        next_button = driver.find_element(By.ID, "pnnext")
                        self._human_click(driver, next_button)
                        time.sleep(random.uniform(2.0, 4.0))
                        page += 1
                    except Exception:
                        log("\nNo more pages available.", self.log_path)
                        break

                return results

        except Exception as e:
            log(f"Error while executing '{query}' in web browser: {e}", self.log_path)
            return []

if __name__ == "__main__":

    query = "medical malpractice lawyers in Connecticut"
    target = 20

    data = FirmParser().scrape_google_places(query, target)

    df = pd.DataFrame(data)

    df.to_csv("google_places_firms.csv", index=False)
