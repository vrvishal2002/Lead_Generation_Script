import os
import re
import time
import random
import requests
from urllib.parse import urlparse, urljoin, quote_plus
from log_lib import log

GOOGLE_PLACES_KEY = os.environ.get("GOOGLE_PLACES_KEY", "")
YELP_API_KEY      = os.environ.get("YELP_API_KEY", "")
SELENIUM_HUB_URL  = os.environ.get("SELENIUM_HUB_URL", "http://localhost:4444")

# Major cities per US state — used to fan out state-level searches into per-city queries
# so we break past Google Maps' ~20-result cap per single search.
_STATE_CITIES = {
    "alabama": ["Birmingham", "Montgomery", "Huntsville", "Mobile", "Tuscaloosa"],
    "alaska": ["Anchorage", "Fairbanks", "Juneau", "Sitka", "Ketchikan"],
    "arizona": ["Phoenix", "Tucson", "Mesa", "Chandler", "Scottsdale", "Glendale", "Tempe"],
    "arkansas": ["Little Rock", "Fayetteville", "Fort Smith", "Springdale", "Jonesboro", "Conway", "Rogers", "Bentonville", "Pine Bluff", "Hot Springs"],
    "california": ["Los Angeles", "San Diego", "San Jose", "San Francisco", "Fresno", "Sacramento", "Oakland", "Bakersfield", "Anaheim", "Santa Ana"],
    "colorado": ["Denver", "Colorado Springs", "Aurora", "Fort Collins", "Lakewood", "Thornton", "Pueblo"],
    "connecticut": ["Bridgeport", "New Haven", "Hartford", "Stamford", "Waterbury"],
    "delaware": ["Wilmington", "Dover", "Newark", "Middletown"],
    "florida": ["Jacksonville", "Miami", "Tampa", "Orlando", "St. Petersburg", "Hialeah", "Tallahassee", "Fort Lauderdale", "Port St. Lucie", "Pembroke Pines"],
    "georgia": ["Atlanta", "Augusta", "Columbus", "Macon", "Savannah", "Athens", "Sandy Springs"],
    "hawaii": ["Honolulu", "Pearl City", "Hilo", "Kailua", "Waipahu"],
    "idaho": ["Boise", "Meridian", "Nampa", "Idaho Falls", "Pocatello"],
    "illinois": ["Chicago", "Aurora", "Rockford", "Joliet", "Naperville", "Springfield", "Peoria"],
    "indiana": ["Indianapolis", "Fort Wayne", "Evansville", "South Bend", "Carmel", "Bloomington"],
    "iowa": ["Des Moines", "Cedar Rapids", "Davenport", "Sioux City", "Iowa City"],
    "kansas": ["Wichita", "Overland Park", "Kansas City", "Topeka", "Olathe"],
    "kentucky": ["Louisville", "Lexington", "Bowling Green", "Owensboro", "Covington"],
    "louisiana": ["New Orleans", "Baton Rouge", "Shreveport", "Lafayette", "Lake Charles"],
    "maine": ["Portland", "Lewiston", "Bangor", "South Portland", "Auburn"],
    "maryland": ["Baltimore", "Frederick", "Rockville", "Gaithersburg", "Bowie", "Annapolis"],
    "massachusetts": ["Boston", "Worcester", "Springfield", "Lowell", "Cambridge", "New Bedford"],
    "michigan": ["Detroit", "Grand Rapids", "Warren", "Sterling Heights", "Ann Arbor", "Lansing", "Flint"],
    "minnesota": ["Minneapolis", "St. Paul", "Rochester", "Duluth", "Bloomington", "Brooklyn Park"],
    "mississippi": ["Jackson", "Gulfport", "Southaven", "Hattiesburg", "Biloxi"],
    "missouri": ["Kansas City", "St. Louis", "Springfield", "Columbia", "Independence"],
    "montana": ["Billings", "Missoula", "Great Falls", "Bozeman", "Butte"],
    "nebraska": ["Omaha", "Lincoln", "Bellevue", "Grand Island", "Kearney"],
    "nevada": ["Las Vegas", "Henderson", "Reno", "North Las Vegas", "Sparks"],
    "new hampshire": ["Manchester", "Nashua", "Concord", "Derry", "Dover"],
    "new jersey": ["Newark", "Jersey City", "Paterson", "Elizabeth", "Trenton", "Camden"],
    "new mexico": ["Albuquerque", "Las Cruces", "Rio Rancho", "Santa Fe", "Roswell"],
    "new york": ["New York City", "Buffalo", "Rochester", "Yonkers", "Syracuse", "Albany"],
    "north carolina": ["Charlotte", "Raleigh", "Greensboro", "Durham", "Winston-Salem", "Fayetteville"],
    "north dakota": ["Fargo", "Bismarck", "Grand Forks", "Minot", "West Fargo"],
    "ohio": ["Columbus", "Cleveland", "Cincinnati", "Toledo", "Akron", "Dayton"],
    "oklahoma": ["Oklahoma City", "Tulsa", "Norman", "Broken Arrow", "Lawton"],
    "oregon": ["Portland", "Eugene", "Salem", "Gresham", "Hillsboro", "Bend"],
    "pennsylvania": ["Philadelphia", "Pittsburgh", "Allentown", "Erie", "Reading", "Scranton"],
    "rhode island": ["Providence", "Cranston", "Warwick", "Pawtucket", "East Providence"],
    "south carolina": ["Columbia", "Charleston", "North Charleston", "Mount Pleasant", "Rock Hill", "Greenville"],
    "south dakota": ["Sioux Falls", "Rapid City", "Aberdeen", "Brookings", "Watertown"],
    "tennessee": ["Memphis", "Nashville", "Knoxville", "Chattanooga", "Clarksville"],
    "texas": ["Houston", "San Antonio", "Dallas", "Austin", "Fort Worth", "El Paso", "Arlington", "Corpus Christi", "Plano", "Lubbock"],
    "utah": ["Salt Lake City", "West Valley City", "Provo", "West Jordan", "Orem"],
    "vermont": ["Burlington", "Essex", "South Burlington", "Colchester", "Rutland"],
    "virginia": ["Virginia Beach", "Norfolk", "Chesapeake", "Richmond", "Newport News", "Alexandria"],
    "washington": ["Seattle", "Spokane", "Tacoma", "Vancouver", "Bellevue", "Kent"],
    "west virginia": ["Charleston", "Huntington", "Morgantown", "Parkersburg", "Wheeling"],
    "wisconsin": ["Milwaukee", "Madison", "Green Bay", "Kenosha", "Racine", "Appleton"],
    "wyoming": ["Cheyenne", "Casper", "Laramie", "Gillette", "Rock Springs"],
}


def _parse_location(query):
    match = re.search(r'\bin\s+(.+)$', query, re.IGNORECASE)
    location = match.group(1).strip() if match else query.strip()
    parts = [p.strip() for p in location.split(",")]
    city  = parts[0] if parts else location
    state = parts[1].strip() if len(parts) > 1 else ""
    return city, state


def _normalise(url):
    try:
        parsed = urlparse(url if "://" in url else "https://" + url)
        netloc = parsed.netloc.lstrip("www.")
        return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Option 1 — Google Places API
# Needs: GOOGLE_PLACES_KEY env var (Places API enabled)
# ─────────────────────────────────────────────────────────────────────────────

def _google_places(query, city, state, target, log_path, status_cb, cancel_event):
    if not GOOGLE_PLACES_KEY:
        log("Google Places: GOOGLE_PLACES_KEY not set — skipping", log_path)
        return []

    log("Firm discovery [1/3]: Google Places API", log_path)
    results, seen = [], set()
    search_q = f"{query} in {city}, {state}" if state else f"{query} in {city}"
    next_token = None

    for _ in range(3):
        if cancel_event and cancel_event.is_set():
            break
        if len(results) >= target:
            break

        params = {"pagetoken": next_token, "key": GOOGLE_PLACES_KEY} if next_token \
                 else {"query": search_q, "key": GOOGLE_PLACES_KEY}
        if next_token:
            time.sleep(2)

        try:
            resp = requests.get(
                "https://maps.googleapis.com/maps/api/place/textsearch/json",
                params=params, timeout=15
            ).json()

            status = resp.get("status")
            if status not in ("OK", "ZERO_RESULTS"):
                log(f"Google Places status: {status} — {resp.get('error_message','')}", log_path)
                return results

            for place in resp.get("results", []):
                if len(results) >= target:
                    break
                place_id = place.get("place_id")
                name = place.get("name", "").strip()
                if not name or not place_id:
                    continue

                detail = requests.get(
                    "https://maps.googleapis.com/maps/api/place/details/json",
                    params={"place_id": place_id, "fields": "website", "key": GOOGLE_PLACES_KEY},
                    timeout=10
                ).json()
                website = detail.get("result", {}).get("website", "")
                if not website:
                    continue

                url = _normalise(website)
                if not url or url in seen:
                    continue
                seen.add(url)
                results.append({"Firm Name": name, "Website": url})
                if status_cb:
                    status_cb(len(results), target)
                log(f"  {len(results)}. {name} — {url}", log_path)

            next_token = resp.get("next_page_token")
            if not next_token:
                break

        except Exception as e:
            log(f"Google Places error: {e}", log_path)
            break

    log(f"Google Places found {len(results)} firms", log_path)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Option 2 — Yelp Fusion API
# Needs: YELP_API_KEY env var
# ─────────────────────────────────────────────────────────────────────────────

def _yelp_firms(city, state, target, log_path, status_cb, cancel_event):
    if not YELP_API_KEY:
        log("Yelp: YELP_API_KEY not set — skipping", log_path)
        return []

    log("Firm discovery [2/3]: Yelp Fusion API", log_path)
    results, seen = [], set()
    location = f"{city}, {state}" if state else city
    offset = 0

    while len(results) < target:
        if cancel_event and cancel_event.is_set():
            break
        try:
            resp = requests.get(
                "https://api.yelp.com/v3/businesses/search",
                headers={"Authorization": f"Bearer {YELP_API_KEY}"},
                params={
                    "term": "law firm attorneys lawyers",
                    "location": location,
                    "categories": "lawyers",
                    "limit": min(50, target - len(results)),
                    "offset": offset,
                },
                timeout=15
            ).json()

            businesses = resp.get("businesses", [])
            if not businesses:
                break

            for biz in businesses:
                if len(results) >= target:
                    break
                biz_id = biz.get("id", "")
                name   = biz.get("name", "").strip()
                if not name or not biz_id:
                    continue

                # Fetch business detail to get actual website
                try:
                    detail = requests.get(
                        f"https://api.yelp.com/v3/businesses/{biz_id}",
                        headers={"Authorization": f"Bearer {YELP_API_KEY}"},
                        timeout=10
                    ).json()
                    website = detail.get("url", "")  # Yelp page fallback
                    # Try to get real firm website from Yelp page
                    yelp_page = requests.get(detail.get("url", ""), timeout=10,
                                             headers={"User-Agent": "Mozilla/5.0"})
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(yelp_page.text, "html.parser")
                    biz_website = soup.select_one("a[href*='biz_website_url']") or \
                                  soup.select_one("p.css-1p9ibgf a")
                    if biz_website:
                        website = biz_website.get("href", website)
                except Exception:
                    website = biz.get("url", "")

                if not website:
                    continue
                url = _normalise(website)
                if not url or "yelp.com" in url or url in seen:
                    continue
                seen.add(url)
                results.append({"Firm Name": name, "Website": url})
                if status_cb:
                    status_cb(len(results), target)
                log(f"  {len(results)}. {name} — {url}", log_path)

            offset += len(businesses)
            if offset >= resp.get("total", 0):
                break

        except Exception as e:
            log(f"Yelp error: {e}", log_path)
            break

    log(f"Yelp found {len(results)} firms", log_path)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Option 3 — Google Maps scraping via Selenium
# Uses existing Selenium Grid (no API key needed)
# ─────────────────────────────────────────────────────────────────────────────

def _google_maps_scrape(query, city, state, target, log_path, status_cb, cancel_event):
    log("Firm discovery [3/3]: Google Maps Selenium scraping", log_path)
    results, seen = [], set()

    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        location = f"{city} {state}".strip()
        search_q = quote_plus(f"law firms lawyers {location}")
        maps_url = f"https://www.google.com/maps/search/{search_q}/"

        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        ]

        def _new_driver():
            if SELENIUM_HUB_URL and SELENIUM_HUB_URL != "http://localhost:4444":
                # Cloud / EC2 — headless Remote via Selenium Grid
                from selenium import webdriver
                from selenium.webdriver.chrome.options import Options
                opts = Options()
                opts.add_argument("--headless=0")
                opts.add_argument("--no-sandbox")
                opts.add_argument("--disable-dev-shm-usage")
                opts.add_argument("--disable-blink-features=AutomationControlled")
                opts.add_argument("--window-size=1280,900")
                opts.add_argument("--disable-gpu")
                opts.add_argument("--disable-extensions")
                opts.add_argument("--blink-settings=imagesEnabled=false")
                opts.add_argument("--renderer-process-limit=2")
                opts.add_argument(f"--user-agent={random.choice(user_agents)}")
                opts.add_experimental_option("excludeSwitches", ["enable-automation"])
                d = webdriver.Remote(command_executor=SELENIUM_HUB_URL, options=opts)
            else:
                # Local dev — visible undetected-chromedriver so you can watch it
                import undetected_chromedriver as uc
                opts = uc.ChromeOptions()
                opts.add_argument("--disable-blink-features=AutomationControlled")
                opts.add_argument("--lang=en")
                opts.add_argument(f"--user-agent={random.choice(user_agents)}")
                d = uc.Chrome(options=opts)
            return d, WebDriverWait(d, 8)

        driver, wait = _new_driver()

        try:
            driver.get(maps_url)
            time.sleep(3)

            # Dismiss cookie consent if present
            for btn_text in ["Accept all", "Reject all", "Accept"]:
                try:
                    driver.find_element(By.XPATH, f"//button[contains(.,'{btn_text}')]").click()
                    time.sleep(1)
                    break
                except Exception:
                    pass

            # ── Phase 1: scroll results panel to collect all card hrefs + names ──
            collected = []   # list of (name, href)
            seen_hrefs = set()
            no_new_count = 0

            log("  Phase 1: collecting firm cards via scroll", log_path)
            while len(collected) < target * 3 and no_new_count < 5:
                if cancel_event and cancel_event.is_set():
                    break

                cards = driver.find_elements(By.CSS_SELECTOR, "a.hfpxzc")
                added = 0
                for card in cards:
                    href  = card.get_attribute("href") or ""
                    label = card.get_attribute("aria-label") or ""
                    name  = label.strip()
                    if href and name and href not in seen_hrefs:
                        seen_hrefs.add(href)
                        collected.append((name, href))
                        added += 1

                log(f"  Scroll: {len(collected)} cards collected (+{added})", log_path)

                if added == 0:
                    no_new_count += 1
                else:
                    no_new_count = 0

                # Check if panel is fully scrolled (end-of-results marker)
                try:
                    driver.find_element(By.CSS_SELECTOR, "p.fontBodyMedium > span > span")
                    log("  Reached end of results panel", log_path)
                    break
                except Exception:
                    pass

                # Scroll the feed panel down
                try:
                    panel = driver.find_element(By.CSS_SELECTOR, "div[role='feed']")
                    driver.execute_script("arguments[0].scrollBy(0, 2500)", panel)
                    time.sleep(2)
                except Exception:
                    break

            log(f"  Phase 1 done: {len(collected)} cards found", log_path)

            # ── Phase 2: visit each place URL to extract website ──
            log("  Phase 2: extracting websites from each firm", log_path)
            for name, href in collected:
                if len(results) >= target:
                    break
                if cancel_event and cancel_event.is_set():
                    break

                website = ""
                for attempt in range(2):
                    try:
                        driver.get(href)
                        time.sleep(2)

                        # Primary: official website button
                        try:
                            web_el = wait.until(EC.presence_of_element_located(
                                (By.CSS_SELECTOR, "a[data-item-id='authority']")
                            ))
                            website = web_el.get_attribute("href") or ""
                        except Exception:
                            pass

                        # Fallback: any non-google external link
                        if not website:
                            for lnk in driver.find_elements(By.CSS_SELECTOR, "a[href^='http']"):
                                h = lnk.get_attribute("href") or ""
                                if h and "google" not in h and "goo.gl" not in h:
                                    website = h
                                    break

                        break  # success — no retry needed

                    except Exception as e:
                        err_str = str(e)
                        if ("session deleted" in err_str or "tab crashed" in err_str
                                or "no such session" in err_str) and attempt == 0:
                            log(f"  Chrome crashed — restarting session", log_path)
                            try:
                                driver.quit()
                            except Exception:
                                pass
                            try:
                                driver, wait = _new_driver()
                            except Exception as restart_err:
                                log(f"  Driver restart failed: {restart_err}", log_path)
                                break
                        else:
                            log(f"  Error fetching {name}: {e}", log_path)
                            break

                if not website:
                    continue

                url = _normalise(website)
                if not url or "google.com" in url or url in seen:
                    continue

                seen.add(url)
                results.append({"Firm Name": name, "Website": url})
                if status_cb:
                    status_cb(len(results), target)
                log(f"  {len(results)}. {name} — {url}", log_path)

        finally:
            try:
                driver.quit()
            except Exception:
                pass

    except Exception as e:
        log(f"Google Maps scrape error: {e}", log_path)

    log(f"Google Maps scrape found {len(results)} firms", log_path)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Google Local tab scraping — &tbm=lcl with #pnnext pagination.
# Uses Selenium Grid (Remote) when SELENIUM_HUB_URL is set (EC2/cloud),
# falls back to local undetected-chromedriver for development.
# ─────────────────────────────────────────────────────────────────────────────

def _google_local_search(query, target, log_path, status_cb, cancel_event):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    log("Firm discovery: Google Local tab scraping (tbm=lcl)", log_path)
    results, seen_names, visited_websites = [], set(), set()

    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ]

    def _make_driver():
        if SELENIUM_HUB_URL and SELENIUM_HUB_URL != "http://localhost:4444":
            # Cloud / EC2 — use Selenium Grid (Selenium 4 API: pass URL string directly)
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            opts = Options()
            opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--disable-blink-features=AutomationControlled")
            opts.add_argument("--window-size=1280,900")
            opts.add_argument("--disable-gpu")
            opts.add_argument(f"--user-agent={random.choice(user_agents)}")
            opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            return webdriver.Remote(command_executor=SELENIUM_HUB_URL, options=opts)
        else:
            # Local dev — use undetected-chromedriver (visible browser)
            import undetected_chromedriver as uc
            opts = uc.ChromeOptions()
            opts.add_argument("--disable-blink-features=AutomationControlled")
            opts.add_argument("--lang=en")
            opts.add_argument(f"--user-agent={random.choice(user_agents)}")
            return uc.Chrome(options=opts)

    driver = None
    try:
        driver = _make_driver()
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined })"
        )
        time.sleep(3)

        url = f"https://www.google.com/search?q={quote_plus(query)}&tbm=lcl"
        log(f"Fetching: {url}", log_path)
        driver.get(url)
        time.sleep(random.uniform(2.0, 4.0))

        if cancel_event and cancel_event.is_set():
            return results

        wait = WebDriverWait(driver, 20)
        page = 1

        while len(results) < target:
            if cancel_event and cancel_event.is_set():
                break

            log(f"\nScraping page {page}...", log_path)

            # Scroll to load all listings on this page
            last_height = driver.execute_script("return document.body.scrollHeight")
            while True:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(3)
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    break
                last_height = new_height

            try:
                wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, "VkpGBb")))
            except Exception:
                log("No listings found on page — stopping.", log_path)
                break

            listings = driver.find_elements(By.CLASS_NAME, "VkpGBb")
            log(f"Found {len(listings)} listings on page {page}.", log_path)

            for item in listings:
                if cancel_event and cancel_event.is_set():
                    break
                try:
                    name = item.text.split("\n")[0].strip()
                    if not name or name in seen_names or "Sponsored" in name:
                        continue
                    seen_names.add(name)

                    driver.execute_script("arguments[0].click();", item)
                    time.sleep(2)

                    website = None
                    try:
                        anchors = item.find_elements(By.TAG_NAME, "a")
                        for a in anchors:
                            if "website" in a.text.strip().lower():
                                raw_href = a.get_attribute("href") or ""
                                if raw_href.startswith("/"):
                                    raw_href = urljoin("https://www.google.com", raw_href)
                                parsed = urlparse(raw_href)
                                if parsed.netloc:
                                    website = f"{parsed.scheme or 'https'}://{parsed.netloc}"
                                break
                    except Exception:
                        website = None

                    if website and website not in visited_websites:
                        visited_websites.add(website)
                        results.append({"Firm Name": name, "Website": website})
                        if status_cb:
                            status_cb(len(results), target)
                        log(f"  {len(results)}. {name} — {website}", log_path)
                    else:
                        log(f"  No website for {name}", log_path)

                    if len(results) >= target:
                        break

                except Exception:
                    continue

            # Navigate to next page
            try:
                next_btn = driver.find_element(By.ID, "pnnext")
                driver.execute_script("arguments[0].click();", next_btn)
                time.sleep(random.uniform(3.0, 5.0))
                page += 1
            except Exception:
                log("\nNo more pages available.", log_path)
                break

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    log(f"Google Local search found {len(results)} firms", log_path)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# FirmParser — tries all 4 options in order until target is met
# ─────────────────────────────────────────────────────────────────────────────

class FirmParser:

    def __init__(self, log_path=None):
        self.log_path = log_path

    def scrape_firms(self, query, target=50, status_callback=None, cancel_event=None):
        if cancel_event and cancel_event.is_set():
            return []

        city, state = _parse_location(query)
        log(f"Firm discovery — city: '{city}', state: '{state}', target: {target}", self.log_path)

        # Detect state-level query: if the "city" portion matches a known state name,
        # fan out into per-city searches to break Google Maps' ~20-result-per-search cap.
        state_key = city.lower()
        cities_for_state = _STATE_CITIES.get(state_key, [])

        results = []
        seen_websites = set()

        if cities_for_state:
            log(f"State-level query detected for '{city}' — searching {len(cities_for_state)} cities", self.log_path)
            # Extract the niche/practice part before "in <location>"
            niche_match = re.search(r'^(.+?)\s+in\s+', query, re.IGNORECASE)
            niche = niche_match.group(1).strip() if niche_match else query.strip()
            per_city_target = max(20, target // len(cities_for_state) + 10)

            for city_name in cities_for_state:
                if cancel_event and cancel_event.is_set():
                    break
                if len(results) >= target:
                    break
                city_query = f"{niche} in {city_name}, {city}"
                log(f"  Searching city: {city_name}", self.log_path)
                city_results = _google_maps_scrape(
                    city_query, city_name, city,
                    per_city_target, self.log_path, status_callback, cancel_event
                )
                for r in city_results:
                    if r["Website"] not in seen_websites:
                        seen_websites.add(r["Website"])
                        results.append(r)
                log(f"  {city_name}: {len(city_results)} found, total so far: {len(results)}", self.log_path)
        else:
            # City-level or unrecognised query — single search
            results = _google_maps_scrape(query, city, state, target, self.log_path, status_callback, cancel_event)

        # # Option 1: Google Places API
        # if not (cancel_event and cancel_event.is_set()):
        #     results = _google_places(query, city, state, target, self.log_path, status_callback, cancel_event)

        # # Option 2: Yelp Fusion API (if Places didn't reach target)
        # if len(results) < target and not (cancel_event and cancel_event.is_set()):
        #     log(f"Places gave {len(results)}/{target} — trying Yelp", self.log_path)
        #     yelp = _yelp_firms(city, state, target - len(results), self.log_path, status_callback, cancel_event)
        #     seen = {r["Website"] for r in results}
        #     results += [r for r in yelp if r["Website"] not in seen]

        # # Google Local tab scraping (non-headless undetected Chrome)
        # if not (cancel_event and cancel_event.is_set()):
        #     results = _google_local_search(query, target, self.log_path, status_callback, cancel_event)

        log(f"Firm discovery complete: {len(results)} firms total", self.log_path)
        return results[:target]

    def scrape_google_places(self, query, target=50, status_callback=None, cancel_event=None):
        return self.scrape_firms(query, target, status_callback, cancel_event)


if __name__ == "__main__":
    import pandas as pd
    query = "medical malpractice and personal injury lawyers in Anchorage, Alaska"
    data = FirmParser().scrape_firms(query, 20)
    print(f"\nFound {len(data)} firms")
    if data:
        pd.DataFrame(data).to_csv("firms_test.csv", index=False)
        print("Saved to firms_test.csv")
        for i, f in enumerate(data, 1):
            print(f"  {i}. {f['Firm Name']} — {f['Website']}")
