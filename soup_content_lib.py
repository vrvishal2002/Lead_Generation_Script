import requests
import re, time, random
import copy
from bs4 import BeautifulSoup
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
import undetected_chromedriver as uc
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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


def get_chrome_driver():

    if driver:
        return driver

    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(f"user-agent={random.choice(user_agents)}")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    driver = uc.Chrome(options=options, driver_executable_path=ChromeDriverManager().install())


    driver = uc.Chrome(
        driver_executable_path=ChromeDriverManager().install())
    time.sleep(3)
    driver.execute_script("""
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined
    })
    """)

    return driver


def get_rendered_soup(url):

    driver = get_chrome_driver()
        
    driver.get(url)
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
    time.sleep(10)
    html = driver.page_source
    driver.quit()
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
            if len(soup.find_all()) < 30 or "not a robot" in str(soup):
                return get_rendered_soup(url)
            return BeautifulSoup(r.text, "html.parser")
    except:
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
        include_name = r"(nav|header)"
        include_element = ["nav", "header"]
        exclude_element = ["footer", "article"]

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
