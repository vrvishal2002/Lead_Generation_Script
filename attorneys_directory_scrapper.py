import time, csv, random
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
import undetected_chromedriver as uc
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from log_lib import log, get_log_name



class FirmDirectoryScrapper:

    def __init__(self, log_path=None):
        self.log_path = log_path

    
    def gather_firms_details(self):

        # Setup driver
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

            BASE_URL = "https://lawyers.findlaw.com/personal-injury-plaintiff/connecticut/"
            driver.get(BASE_URL)
            log(BASE_URL, self.log_path)

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
            time.sleep(3)

            # Get all city links
            city_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/connecticut/']")

            city_urls = []
            for city in city_links:
                link = city.get_attribute("href")
                if link and link not in city_urls:
                    city_urls.append(link)

            log(f"Total cities: {len(city_urls)}", self.log_path)


            results = []

            # Loop through each city
            for city_url in city_urls:
                driver.get(city_url)
                time.sleep(3)

                print(f"Scraping city: {city_url}")

                # Get all firm links
                firm_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/lawfirm/']")
                
                firm_urls = []
                for firm in firm_links:
                    link = firm.get_attribute("href")
                    if link and link not in firm_urls:
                        firm_urls.append(link)

                # Loop firms
                for firm_url in firm_urls:
                    try:
                        driver.get(firm_url)
                        time.sleep(2)

                        # Firm Name
                        try:
                            name = driver.find_element(By.TAG_NAME, "h1").text
                        except:
                            name = ""

                        # Phone
                        try:
                            phone = driver.find_element(By.CSS_SELECTOR, "a[href^='tel']").text
                        except:
                            phone = ""

                        # Email (may not always exist)
                        try:
                            email = driver.find_element(By.CSS_SELECTOR, "a[href^='mailto']").text
                        except:
                            email = ""

                        results.append({
                            "name": name,
                            "phone": phone,
                            "email": email,
                            "url": firm_url
                        })

                        log(f"✔ {name}", self.log_path)

                    except Exception as e:
                        log(f"Error: {e}", self.log_path)

            # Save to CSV
            with open("lawyers.csv", "w", newline="", encoding="utf-8") as file:
                writer = csv.DictWriter(file, fieldnames=["name", "phone", "email", "url"])
                writer.writeheader()
                writer.writerows(results)

        
        finally:
            if driver:
                try:
                    driver.quit()
                except OSError:
                    pass
                del driver  # explicitly remove the object
        
        return driver
    



FirmDirectoryScrapper(log_path=get_log_name()).gather_firms_details()
print("Done! Data saved to lawyers.csv")
