from contextlib import contextmanager

import requests
import re, time, random
import copy
from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import threading

from log_lib import log


# =====================================================
# BASIC FETCH
# =====================================================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
}
user_agents = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119 Safari/537.36"
]
log_path = None
driver = None
DRIVERS_COUNT = 5
selenium_driver_lock = threading.Semaphore(DRIVERS_COUNT)


@contextmanager
def selenium_chrome_driver():
    with selenium_driver_lock:
        time.sleep(random.uniform(0.5, 1.5))
        chrome_options = Options()
        
        # 1. Standard stability arguments
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        
        # 2. The "Stealth" layer: Remove the 'automation' flag from the browser's JS
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        
        # 3. Exclude the switches that Chrome uses to announce it's a bot
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # 4. Force a real-looking Window size (bots often have 0x0 or weird ratios)
        chrome_options.add_argument("--window-size=1920,1080")

        driver = webdriver.Remote(
            command_executor='http://13.235.23.148:4444',
            options=chrome_options
        )
        
        # 5. Overwrite the navigator.webdriver property to 'undefined' via JS injection
        # This is the final step to make the "Not Secure" error go away.
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            """
        })

        try:
            yield driver
        finally:
            driver.quit()
            

# def get_chrome_driver():

#     if driver:
#         return driver

#     options = uc.ChromeOptions()
#     options.add_argument("--headless=new")
#     options.add_argument("--disable-gpu")
#     options.add_argument("--no-sandbox")
#     options.add_argument("--disable-dev-shm-usage")
#     options.add_argument("--window-size=1920,1080")
#     options.add_argument("--disable-blink-features=AutomationControlled")
#     options.add_argument(f"user-agent={random.choice(user_agents)}")
#     options.add_experimental_option("excludeSwitches", ["enable-automation"])
#     options.add_experimental_option('useAutomationExtension', False)
#     driver = uc.Chrome(options=options, driver_executable_path=ChromeDriverManager().install())


#     driver = uc.Chrome(
#         driver_executable_path=ChromeDriverManager().install())
#     time.sleep(3)
#     driver.execute_script("""
#     Object.defineProperty(navigator, 'webdriver', {
#         get: () => undefined
#     })
#     """)

#     return driver


def get_rendered_soup(url):

    # driver = get_chrome_driver()
    with selenium_chrome_driver() as driver:
        driver.get(url)
        if 'not a robot' in driver.page_source:
            # wait for iframe
            log(f"{url}: Encountered anti-bot, waiting for captcha to load...", log_path)
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
        time.sleep(10)
        html = driver.page_source
        return BeautifulSoup(html, "html.parser")


def close_driver():
    if driver:
        try:
            driver.quit()
            log("Closed Chrome driver", log_path)
        except:
            pass


def get_soup(url, params=None):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10, params=params)
        if r.status_code and r.text:
            soup = BeautifulSoup(r.text, "html.parser")
            if len(soup.find_all()) < 50 or "not a robot" in str(soup):
                return get_rendered_soup(url)
            return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log(f"Error while fetching {url}: {e}, So trying with rendered soup", log_path)
        try:
            return get_rendered_soup(url)
        except Exception as e2:
            log(f"Error while fetching rendered soup for {url}: {e2}", log_path)
            return None
    return None


def check_email_in_search_results(emails):
    query = f'{" ".join([f"\"{email}\" or " for email in emails])}'
    url = f"https://www.google.com/search?q={query}"
    soup = get_soup(url)
    results = soup.find_all(class_="N54PNb")

    for result in results:

        show_result_with_element = result.find_all(class_="TXwUJf")
        for exclude_tag in show_result_with_element:
            exclude_tag.decompose()
        
        text = result.get_text(" ", strip=True)
        for email in emails:
            if email.lower() in text.lower():
                link_tag = result.find("a", href=True)
                if link_tag:
                    return email, link_tag["href"]
            
    return None, None
    

# =====================================================
# CLEAN DOM (REMOVE NAV CONTAMINATION)
# =====================================================

def clean_dom(soup):
    exclude_elements = ["nav", "header", "footer", "script", "style"]

    content_pattern = re.compile(r"content|main|article", re.I)

    for tag in soup.find_all(exclude_elements):

        # ✅ Skip if inside <article> or <main>
        if tag.find_parent(["article", "main"]):
            continue

        # ✅ Skip if inside a content container
        parent = tag.find_parent()
        while parent:
            class_attr = " ".join(parent.get("class", []))
            id_attr = parent.get("id", "")

            if content_pattern.search(class_attr) or content_pattern.search(id_attr):
                break

            parent = parent.parent
        else:
            # ❌ Not inside protected container → remove
            tag.decompose()

    return soup


def get_main_content(soup, is_home_page = False):
    combined = soup.new_tag("div")
    combined["id"] = "combined-content"

    include_name = r"(content|main|article|hero)"
    include_element = ["main", "article"]
    exclude_element = ["header", "footer"]
    if is_home_page:
        return soup

    pattern = re.compile(include_name, re.I)

    for tag in soup.find_all(True):

        class_attr = " ".join(tag.get("class", []))
        id_attr = tag.get("id", "")

        if (
            pattern.search(class_attr)
            or pattern.search(id_attr)
            or tag.name in include_element
        ):
            combined.append(copy.copy(tag))

    # ✅ If we found content → return combined
    if combined.contents:
        return combined

    # ❌ If empty → remove header & footer and return rest of soup
    for tag in soup.find_all(exclude_element):
        tag.decompose()
    return soup
