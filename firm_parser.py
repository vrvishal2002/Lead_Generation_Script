from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
import undetected_chromedriver as uc
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import pandas as pd
import time
import random
from urllib.parse import urlparse, parse_qs, urljoin
from log_lib import log


class FirmParser:
    
    def __init__(self, log_path=None):
        self.log_path = log_path


    def scrape_google_places(self, query, target=50):

        chrome_options = uc.ChromeOptions()

        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--lang=en")
        chrome_options.add_argument("--headless=new")


        user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119 Safari/537.36"
        ]

        chrome_options.add_argument(f"user-agent={random.choice(user_agents)}")
        
        driver = None
        try:
            driver = uc.Chrome(driver_executable_path=ChromeDriverManager().install())
            time.sleep(3)
            driver.execute_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            })
            """)

            url = f"https://www.google.com/search?q={query}&tbm=lcl"
            log(url, self.log_path)
            driver.get(url)
            log(url, self.log_path)

            if 'robot' in driver.page_source:
                # wait for iframe
                iframe = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//iframe[contains(@src,'recaptcha')]"))
                )

                driver.switch_to.frame(iframe)

                # click checkbox
                checkbox = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.ID, "recaptcha-anchor"))
                )

                checkbox.click()
                driver.switch_to.default_content()

            wait = WebDriverWait(driver, 20)

            results = []
            seen_names = set()
            visited_websites = set()
            page = 1

            while len(results) < target:
                time.sleep(20)
                log(f"\nScraping page {page}...", self.log_path)
                last_height = driver.execute_script("return document.body.scrollHeight")

                while True:
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(3)  # wait for new listings to load
                    new_height = driver.execute_script("return document.body.scrollHeight")
                    if new_height == last_height:
                        break
                    last_height = new_height
                    # Wait until listings load
                
                try:
                    wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, "VkpGBb")))
                except:
                    log(f"No listings found on {page} page.", self.log_path)
                    break

                listings = driver.find_elements(By.CLASS_NAME, "VkpGBb")
                log(f"Found {len(listings)} listings.", self.log_path)

                for item in listings:
                    try:
                        name = item.text.split("\n")[0].strip()

                        if name in seen_names or "Sponsored" in name:
                            continue

                        seen_names.add(name)

                        # Click listing
                        driver.execute_script("arguments[0].click();", item)
                        time.sleep(2)

                        # Extract website link
                        website = None

                        try:
                            # Search ONLY inside current item
                            anchors = item.find_elements(By.TAG_NAME, "a")
                            for a in anchors:
                                text = a.text.strip().lower()
                                if "website" in text:

                                    raw_href = a.get_attribute("href")
                                    log("raw_href", self.log_path)
                                    if raw_href:
                                        # If relative (like /aclk...)
                                        if raw_href.startswith("/"):
                                            raw_href = urljoin("https://www.google.com", raw_href)


                                        parsed = urlparse(raw_href)

                                        if parsed and parsed.netloc:
                                            website = f"{parsed.scheme or 'https'}://{parsed.netloc}"
                                        else:
                                            website = None
                                    break

                        except:
                            website = None

                        if website and website not in visited_websites:
                            visited_websites.add(website)
                            results.append({
                                "Firm Name": name,
                                "Website": website
                            })
                            log(f"{len(results)}.Collected: {name}", self.log_path)

                        else:
                            log(f"No website for {name}", self.log_path)


                        if len(results) >= target:
                            break

                    except Exception as e:
                        continue

                # Try clicking Next Page (the real Places pagination)
                try:
                    next_button = driver.find_element(By.ID, "pnnext")

                    driver.execute_script("arguments[0].click();", next_button)
                    time.sleep(3)

                    page += 1

                except:
                    log("\nNo more pages available.", self.log_path)
                    break
            return results

        finally:
            if driver:
                try:
                    driver.quit()
                except OSError:
                    pass
                del driver  # explicitly remove the object

if __name__ == "__main__":

    query = "medical malpractice lawyers in Connecticut"
    target = 20

    data = FirmParser().scrape_google_places(query, target)

    df = pd.DataFrame(data)

    df.to_csv("google_places_firms.csv", index=False)

