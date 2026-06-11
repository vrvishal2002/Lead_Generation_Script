from contextlib import contextmanager

import os
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

SELENIUM_HUB_URL = os.environ.get("SELENIUM_HUB_URL", "http://52.139.236.109:4444")

# =====================================================
# BASIC FETCH
# =====================================================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}

# Chrome 131/132 user agents — matching selenium/node-chrome:4.27.0
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
]

log_path = None
driver = None
DRIVERS_COUNT = int(os.environ.get("SELENIUM_DRIVERS_COUNT", "6"))
selenium_driver_lock = threading.Semaphore(DRIVERS_COUNT)

# Domains where both HTTP and Selenium fetching permanently failed (DNS error, SSL mismatch, etc.).
# Populated during the job and checked before each fetch to avoid re-attempting unreachable sites.
_dead_domains: set = set()
_dead_domains_lock = threading.Lock()

# Timeout (seconds) for establishing a new Remote WebDriver session with the selenium-grid.
_WEBDRIVER_CONNECT_TIMEOUT = 30

# In-memory Google cookie cache — shared across all sessions in this process
_google_cookies: list = []
_cookie_lock = threading.Lock()


def _chrome_version(ua: str) -> str:
    m = re.search(r"Chrome/(\d+)", ua)
    return m.group(1) if m else "131"


def _apply_stealth(driver, ua: str):
    """Deep CDP stealth — patches fingerprinting vectors Google actively checks."""
    ver = _chrome_version(ua)
    brand_list = f'"Google Chrome";v="{ver}", "Chromium";v="{ver}", "Not_A Brand";v="24"'

    # Network-level headers so every request looks like a real browser
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": {
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": brand_list,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
        }})
    except Exception:
        pass

    # Lock timezone + locale so they match the US persona
    try:
        driver.execute_cdp_cmd("Emulation.setTimezoneOverride", {"timezoneId": "America/New_York"})
    except Exception:
        pass
    try:
        driver.execute_cdp_cmd("Emulation.setLocaleOverride", {"locale": "en-US"})
    except Exception:
        pass

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": f"""
        (() => {{
            // 1. Remove webdriver flag
            Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
            try {{ delete navigator.__proto__.webdriver; }} catch(_) {{}}

            // 2. Full chrome runtime object (headless Chrome has an empty one)
            window.chrome = {{
                app: {{ isInstalled: false,
                    InstallState: {{ DISABLED:'disabled', INSTALLED:'installed', NOT_INSTALLED:'not_installed' }},
                    RunningState: {{ CANNOT_RUN:'cannot_run', READY_TO_RUN:'ready_to_run', RUNNING:'running' }} }},
                runtime: {{
                    OnInstalledReason: {{ CHROME_UPDATE:'chrome_update', INSTALL:'install', SHARED_MODULE_UPDATE:'shared_module_update', UPDATE:'update' }},
                    PlatformOs: {{ ANDROID:'android', CROS:'cros', LINUX:'linux', MAC:'mac', WIN:'win' }}
                }}
            }};

            // 3. Language + vendor
            Object.defineProperty(navigator, 'languages', {{ get: () => ['en-US', 'en'] }});
            Object.defineProperty(navigator, 'vendor',    {{ get: () => 'Google Inc.' }});

            // 4. Realistic plugin list
            Object.defineProperty(navigator, 'plugins', {{
                get: () => [
                    {{ name:'Chrome PDF Plugin',  filename:'internal-pdf-viewer',              description:'Portable Document Format' }},
                    {{ name:'Chrome PDF Viewer',  filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai', description:'' }},
                    {{ name:'Native Client',      filename:'internal-nacl-plugin',             description:'' }}
                ]
            }});

            // 5. Hardware fingerprint
            Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => 8 }});
            Object.defineProperty(navigator, 'deviceMemory',        {{ get: () => 8 }});
            Object.defineProperty(navigator, 'platform',            {{ get: () => 'Win32' }});
            Object.defineProperty(navigator, 'maxTouchPoints',      {{ get: () => 0 }});

            // 6. Screen / window consistency with 1920×1080 viewport
            Object.defineProperty(screen, 'width',       {{ get: () => 1920 }});
            Object.defineProperty(screen, 'height',      {{ get: () => 1080 }});
            Object.defineProperty(screen, 'availWidth',  {{ get: () => 1920 }});
            Object.defineProperty(screen, 'availHeight', {{ get: () => 1040 }});
            Object.defineProperty(screen, 'colorDepth',  {{ get: () => 24 }});
            Object.defineProperty(screen, 'pixelDepth',  {{ get: () => 24 }});
            Object.defineProperty(window, 'outerWidth',  {{ get: () => 1920 }});
            Object.defineProperty(window, 'outerHeight', {{ get: () => 1080 }});

            // 7. Network connection — looks like a wifi desktop
            Object.defineProperty(navigator, 'connection', {{
                get: () => ({{ rtt:50, type:'wifi', downlink:10.0, downlinkMax:Infinity,
                               effectiveType:'4g', saveData:false, onchange:null }})
            }});

            // 8. Notification permission probe
            const _origQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = p =>
                p.name === 'notifications'
                    ? Promise.resolve({{ state: Notification.permission }})
                    : _origQuery(p);

            // 9. WebGL vendor / renderer
            const _getParam = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(p) {{
                if (p === 37445) return 'Intel Inc.';
                if (p === 37446) return 'Intel Iris OpenGL Engine';
                return _getParam.call(this, p);
            }};

            // 10. Canvas fingerprint — imperceptible per-session noise
            const _toDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {{
                const data = _toDataURL.apply(this, arguments);
                if (this.getContext && this.getContext('2d')) {{
                    const nonce = (Math.random() * 0.1).toString(36).slice(2, 6);
                    return data.slice(0, -4) + nonce + data.slice(-4);
                }}
                return data;
            }};

            // 11. AudioContext fingerprint — tiny random noise
            if (window.AudioBuffer) {{
                const _getChannelData = AudioBuffer.prototype.getChannelData;
                AudioBuffer.prototype.getChannelData = function() {{
                    const arr = _getChannelData.apply(this, arguments);
                    for (let i = 0; i < arr.length; i += 100)
                        arr[i] += (Math.random() - 0.5) * 0.0001;
                    return arr;
                }};
            }}

            // 12. Remove $cdc_ / $chrome_ properties left by ChromeDriver
            for (const key of Object.getOwnPropertyNames(window)) {{
                if (key.startsWith('$cdc_') || key.startsWith('$chrome_asyncScriptInfo'))
                    try {{ delete window[key]; }} catch(_) {{}}
            }}

            // 13. Patch iframe contentWindow so inner frames also hide webdriver
            Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {{
                get: function() {{
                    const w = Object.getOwnPropertyDescriptor(
                        HTMLIFrameElement.prototype, 'contentWindow').get.call(this);
                    if (w) try {{ Object.defineProperty(w.navigator, 'webdriver', {{ get: () => undefined }}); }} catch(_) {{}}
                    return w;
                }}
            }});

            // 14. Slight timing jitter to defeat high-resolution timing attacks
            const _now = Date.now;
            Date.now = () => _now() + Math.floor(Math.random() * 3);
        }})();
    """})


def _save_google_cookies(driver):
    try:
        cookies = driver.get_cookies()
        with _cookie_lock:
            global _google_cookies
            _google_cookies = cookies
    except Exception:
        pass


def _load_google_cookies(driver) -> bool:
    with _cookie_lock:
        cookies = list(_google_cookies)
    if not cookies:
        return False
    for c in cookies:
        try:
            driver.add_cookie(c)
        except Exception:
            pass
    return True


def human_scroll_to_bottom(driver, cancel_event=None) -> int:
    """Scroll down in human-sized steps instead of jumping to the bottom."""
    last_height = driver.execute_script("return document.body.scrollHeight")
    current_pos = driver.execute_script("return window.pageYOffset")

    while True:
        if cancel_event and cancel_event.is_set():
            break

        step = random.randint(200, 500)
        current_pos += step
        driver.execute_script(
            f"window.scrollTo({{top: {current_pos}, behavior: 'smooth'}});"
        )
        time.sleep(random.uniform(0.4, 1.0))

        new_height = driver.execute_script("return document.body.scrollHeight")
        at_bottom = driver.execute_script(
            "return (window.innerHeight + Math.round(window.pageYOffset)) >= document.body.scrollHeight - 50"
        )
        if at_bottom and new_height == last_height:
            break
        last_height = new_height

    return last_height


@contextmanager
def selenium_chrome_driver():
    with selenium_driver_lock:
        time.sleep(random.uniform(1.5, 4.0))
        ua = random.choice(USER_AGENTS)
        chrome_options = Options()

        # Stability
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")

        # Stealth flags
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument(f"--user-agent={ua}")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("--disable-infobars")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--lang=en-US")

        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)
        chrome_options.add_experimental_option("prefs", {
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
        })

        from selenium.webdriver.remote.remote_connection import RemoteConnection
        conn = RemoteConnection(SELENIUM_HUB_URL, resolve_ip=False)
        conn.set_timeout(_WEBDRIVER_CONNECT_TIMEOUT)
        driver = webdriver.Remote(
            command_executor=conn,
            options=chrome_options
        )

        _apply_stealth(driver, ua)

        try:
            # Warmup on google.com — establishes cookies + looks like a returning user
            # Simulate coming from a different source or direct entry
            driver.get("https://www.google.com/")
            time.sleep(random.uniform(2.5, 5.0))
            
            # Human-like interaction: Type into the search box even if we go to a URL directly later
            try:
                search_box = driver.find_element(By.NAME, "q")
                for char in "google maps":
                    search_box.send_keys(char)
                    time.sleep(random.uniform(0.1, 0.3))
            except: pass

            # Handle initial warmup CAPTCHA if triggered by Cloud IP
            if 'not a robot' in driver.page_source.lower():
                try:
                    iframes = driver.find_elements(By.XPATH, "//iframe[contains(@src,'recaptcha')]")
                    if iframes:
                        driver.switch_to.frame(iframes[0])
                        checkbox = driver.find_element(By.ID, "recaptcha-anchor")
                        checkbox.click()
                        driver.switch_to.default_content()
                        time.sleep(random.uniform(3.0, 5.0))
                except: pass

            # Reload with stored cookies from a previous session in this process
            if _load_google_cookies(driver):
                driver.refresh()
                time.sleep(random.uniform(2.0, 3.5))

            # Simulate a human glancing at the homepage — small scroll then scroll back
            scroll_y = random.randint(80, 280)
            driver.execute_script(f"window.scrollTo({{top: {scroll_y}, behavior: 'smooth'}});")
            time.sleep(random.uniform(1.5, 3.0))
            driver.execute_script("window.scrollTo({top: 0, behavior: 'smooth'});")
            time.sleep(random.uniform(1.0, 2.0))

            # Persist cookies for the next session created within this process
            _save_google_cookies(driver)

            yield driver
        finally:
            try:
                driver.quit()
            except Exception:
                pass
            

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
    with selenium_chrome_driver() as driver:
        driver.get(url)
        if 'not a robot' in driver.page_source:
            log(f"{url}: Encountered anti-bot, waiting for captcha to load...", log_path)
            iframe = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//iframe[contains(@src,'recaptcha')]"))
            )
            driver.switch_to.frame(iframe)
            checkbox = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "recaptcha-anchor"))
            )
            checkbox.click()
            driver.switch_to.default_content()
        time.sleep(5)
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
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.replace("www.", "")

    with _dead_domains_lock:
        if domain in _dead_domains:
            return None

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
            if domain:
                with _dead_domains_lock:
                    _dead_domains.add(domain)
                log(f"Marked {domain} as permanently unreachable — will skip in future calls", log_path)
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
