import re, string, random, requests, os
import smtplib
import time
import dns.resolver
import threading
import socket, ssl
import socks
from log_lib import log
from soup_content_lib import selenium_chrome_driver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
import time, traceback, random

# Ensure these match the user 'proxyuser' and password you created on your Ubuntu VPS
PROXY_HOST = os.environ.get("SMTP_PROXY_HOST", "166.0.192.58")
PROXY_PORT = int(os.environ.get("SMTP_PROXY_PORT", 1080))
PROXY_USER = os.environ.get("SMTP_PROXY_USER", "proxyuser")
PROXY_PASS = os.environ.get("SMTP_PROXY_PASS", "Proxyuser")

MAX_ITERATIONS = 4
USER_EMAIL = "vrvishalmrf@yahoo.com"
VALIDATING_USER_DOMAINS = [
        "gmail.com",
        "yahoo.com"
]
DISPOSABLE_DOMAINS =  {
        "mailinator.com",
        "tempmail.com",
        "10minutemail.com",
        "guerrillamail.com",
        "protectingpatientrights.com",
        "cellinolaw.com",
        "cartermario.com",
        "brandonjbroderick.com",
        "carmodylaw.com",
        "danaherlagnese.com",
        "brownandcrouppen.com",
        "devaultlaw.com",
        "www.devaultlaw.com"
}

mx_cache = {}
user_domain_lock = threading.Lock()
mx_cache_lock = threading.Lock()
domain_locks = {}
domain_locks_lock = threading.Lock()
email_pattern_for_domain = {}
email_pattern_lock = threading.Lock()
ms_request_semaphore = threading.Semaphore(4)


class EmailVerifier:
    

    def __init__(self, log_path=None, cancel_event=None):
        self.timeout = 10
        self.log_path = log_path
        self.cancel_event = cancel_event


    def clean_domain(self, domain):
        domain = domain.lower()
        domain = domain.replace("https://", "").replace("http://", "")
        return domain.strip("/")
    

    def get_domain_lock(self, domain):
        with domain_locks_lock:
            if domain not in domain_locks:
                domain_locks[domain] = threading.Semaphore(3)
            return domain_locks[domain]

        
    def parse_full_name(self, full_name):
        """
        Splits full name into:
        first, middle (optional), last
        """

        # Remove commas, dots
        full_name = full_name.lower()
        full_name = full_name.replace(",", "").replace(".", "")
        if len(full_name) > 2 and "jr" in full_name[-2:]:
            full_name = full_name.replace("jr", "")
        if len(full_name) > 3 and "esq" in full_name[-3:]:
            full_name = full_name.replace("esq", "")
        if len(full_name) > 2 and "dr" in full_name[:2]:
            full_name = full_name[2:]

        # Remove extra spaces
        full_name = re.sub(r"\s+", " ", full_name.strip())

        parts = full_name.split(" ")

        if len(parts) == 1:
            return parts[0], None, None

        if len(parts) == 2:
            return parts[0], None, parts[1]

        # More than 2 → first, middle, last
        first = parts[0]
        last = parts[-1]
        middle = " ".join(parts[1:-1])

        return first, middle, last


    def extract_middle_parts(self, middle_name):
        if not middle_name:
            return None, []

        # Remove dots
        middle = re.sub(r"[.]", "", middle_name.lower()).strip()
        parts = middle.split()

        initials = [p[0] for p in parts if p]
        full_middle = "".join(parts) if any(len(p) > 1 for p in parts) else None

        return full_middle, initials


    def detect_pattern(self, full_name, email):

        local = email.split("@")[0]
        domain = email.split("@")[1]

        first, middle, last = self.parse_full_name(full_name)

        if not last:
            return None

        first = first.lower()
        last = last.lower()
        f = first[0]
        l = last[0]

        full_middle, middle_initials = self.extract_middle_parts(middle)

        patterns = {}
        self.first_second_name_pattern(patterns, first, last, domain)

        for mi in middle_initials:
            patterns.update({
                "{first}.{mi}.{last}@{domain}":f"{first}.{mi}.{last}@{domain}",
                "{first}{mi}{last}@{domain}":f"{first}{mi}{last}@{domain}",
                "{f}{mi}{last}@{domain}":f"{f}{mi}{last}@{domain}",
                "{first}.{mi}{last}@{domain}":f"{first}.{mi}{last}@{domain}",
                "{first}{mi}.{last}@{domain}":f"{first}{mi}.{last}@{domain}",
                "{f}.{mi}.{last}@{domain}":f"{f}.{mi}.{last}@{domain}",
                "{f}.{mi}{last}@{domain}":f"{f}.{mi}{last}@{domain}",
                "{f}{mi}.{last}@{domain}":f"{f}{mi}.{last}@{domain}",
                "{f}{mi}{l}@{domain}":f"{f}{mi}{l}@{domain}",
            })


        # Full middle
        if full_middle:
            patterns.update({
                "{first}.{full_middle}.{last}@{domain}":f"{first}.{full_middle}.{last}@{domain}",
                "{first}{full_middle}{last}@{domain}":f"{first}{full_middle}{last}@{domain}",
            })

        for key, value in patterns.items():
            if value.split("@")[0] == local:
                return key
        return None


    def first_second_name_pattern(self, patterns, first, last, domain):
        f = first[0]
        l = last[0]
        patterns.update({
            "{first}@{domain}":f"{first}@{domain}",
            "{last}@{domain}":f"{last}@{domain}",
            "{first}.{last}@{domain}":f"{first}.{last}@{domain}",
            "{first}_{last}@{domain}":f"{first}_{last}@{domain}",
            "{first}{last}@{domain}":f"{first}{last}@{domain}",
            "{f}{last}@{domain}":f"{f}{last}@{domain}",
            "{f}_{last}@{domain}":f"{f}_{last}@{domain}",
            "{f}.{last}@{domain}":f"{f}.{last}@{domain}",
            "{first}{l}@{domain}":f"{first}{l}@{domain}",
            "{first}.{l}@{domain}":f"{first}.{l}@{domain}",
            "{last}.{first}@{domain}":f"{last}.{first}@{domain}",
            "{last}{first}@{domain}":f"{last}{first}@{domain}",
            "{f}{l}@{domain}":f"{f}{l}@{domain}",
            "{f}_{l}@{domain}":f"{f}_{l}@{domain}",
        })



    def generate_email_patterns(self, full_name, domain, use_pattern_learned = True):
        domain = self.clean_domain(domain)
        patterns=None
        email_patterns = []
        if domain in email_pattern_for_domain and use_pattern_learned:
            patterns = email_pattern_for_domain[domain]

        full_name = full_name.lower().replace(",", "").replace(".", "").replace("-", " ").strip()
        if len(full_name.split()) == 1:
            return [f"{full_name.lower()}@{domain}"]

        first, middle, last = self.parse_full_name(full_name)

        if not last:
            return []

        first = first.lower()
        last = last.lower()

        f = first[0]
        l = last[0]

        full_middle, middle_initials = self.extract_middle_parts(middle)

        mapping = {}

        # Basic patterns
        self.first_second_name_pattern(mapping, first, last, domain)

        if patterns and not full_middle and not middle_initials:
            email_patterns = [mapping[pattern] for pattern in patterns if pattern in mapping]

        
        # Middle initials
        for mi in middle_initials:
            middle_part_pattern = {
                "{first}.{mi}.{last}@{domain}":f"{first}.{mi}.{last}@{domain}",
                "{first}{mi}{last}@{domain}":f"{first}{mi}{last}@{domain}",
                "{f}{mi}{last}@{domain}":f"{f}{mi}{last}@{domain}",
                "{first}.{mi}{last}@{domain}":f"{first}.{mi}{last}@{domain}",
                "{first}{mi}.{last}@{domain}":f"{first}{mi}.{last}@{domain}",
                "{f}.{mi}.{last}@{domain}":f"{f}.{mi}.{last}@{domain}",
                "{f}.{mi}{last}@{domain}":f"{f}.{mi}{last}@{domain}",
                "{f}{mi}.{last}@{domain}":f"{f}{mi}.{last}@{domain}",
                "{f}{mi}{l}@{domain}":f"{f}{mi}{l}@{domain}",
            }
            if patterns:
                mid_email_patterns = [middle_part_pattern[pattern] for pattern in patterns if pattern in middle_part_pattern]
                if not email_patterns:
                    email_patterns = mid_email_patterns
                else:
                    for email_pattern in mid_email_patterns:
                        if email_pattern not in email_patterns:
                            email_patterns.append(email_pattern)
                
            mapping.update(middle_part_pattern)

        

        # Full middle
        if full_middle:
            full_middle_part_pattern = {
                "{first}.{full_middle}.{last}@{domain}":f"{first}.{full_middle}.{last}@{domain}",
                "{first}{full_middle}{last}@{domain}":f"{first}{full_middle}{last}@{domain}",
            }
            if patterns:
                full_email_patterns = [full_middle_part_pattern[pattern] for pattern in patterns if pattern in full_middle_part_pattern]
                if not email_patterns:
                    email_patterns = full_email_patterns
                else:
                    for email_pattern in full_email_patterns:
                        if email_pattern not in email_patterns:
                            email_patterns.append(email_pattern)
            mapping.update(full_middle_part_pattern)

        if use_pattern_learned and email_patterns:
            return email_patterns
        return list(mapping.values())


    # 1. Syntax validation
    def validate_syntax(self, emails):
        pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
        return [email for email in emails if re.match(pattern, email)]


    # 3. MX record check
    def get_mx_records(self, domain):

        mx_records = mx_cache.get(domain)
        if mx_records:
            return mx_records

        try:

            for attempt in range(2):
                resolver = dns.resolver.Resolver()
                resolver.lifetime = 3
                records = resolver.resolve(domain, "MX")
                mx_records = sorted([(r.preference, str(r.exchange)) for r in records])

                if mx_records:
                    with mx_cache_lock:
                        mx_cache[domain] = mx_records
                    
                    return mx_records

        except:
            return []
        
    
    def smtp_batch_check(self, emails, mx_hosts, itr=0, results=None, stuck_check = False):
        if results is None:
            results = {}

        if self.cancel_event and self.cancel_event.is_set():
            return None, results, itr

        # remove already checked emails
        remaining = [e for e in emails if e not in results]

        if not remaining:
            return None, results, itr
        
        domain = remaining[0].split("@")[1]

        if len(mx_hosts) > 2:
            mx_hosts = mx_hosts[:2]

        pref, mx = mx_hosts[0]
        try:
            mx = mx.rstrip(".")
            lock = self.get_domain_lock(domain)


            with lock:

                time.sleep(random.uniform(0.1, 0.5))

                try:
                    if PROXY_HOST and PROXY_PORT and PROXY_USER and PROXY_PASS:
                        # Establish connection through the SOCKS5 proxy
                        s = socks.socksocket()
                        s.set_proxy(
                            socks.SOCKS5, 
                            PROXY_HOST, 
                            PROXY_PORT,
                            rdns=True,
                            username=PROXY_USER,
                            password=PROXY_PASS
                        )
                        s.settimeout(self.timeout)
                        s.connect((mx, 25))
                        
                        server = smtplib.SMTP()
                        server.timeout = self.timeout
                        server.sock = s
                        server._host = mx  # Essential for STARTTLS SNI in Python 3.12+
                        
                        # Read the server's initial 220 greeting
                        server.getreply()
                    else:
                        # server = smtplib.SMTP(mx, 25, timeout=self.timeout)
                        pass
                except (socks.ProxyConnectionError, socks.GeneralProxyError) as pe:
                    log(f"CRITICAL: Proxy at {PROXY_HOST}:{PROXY_PORT} is DOWN or REJECTING: {pe}", self.log_path)
                    return None, results, itr
                except Exception as e:
                    log(f"Failed to connect to {mx} (Proxy: {bool(PROXY_HOST)}): {e}", self.log_path)
                    return None, results, itr

                try:
                    USER_DOMAIN = random.choice(VALIDATING_USER_DOMAINS)
                    code, _ = server.ehlo(USER_DOMAIN) 

                    if server.has_extn("STARTTLS"):
                        server.starttls()
                        server.ehlo(USER_DOMAIN)

                    sender = f"admin@{[domain, USER_DOMAIN][itr % 2]}"
                    code, _ = server.mail(sender)
                    if code != 250:
                         raise smtplib.SMTPSenderRefused(code, _, sender)

                    for email in remaining:
                        code, message = server.rcpt(email)

                        results[email] = code

                        if code in [250, 251]:
                            log(f"Email: {email} verified with code: {code} in iteration {itr}", self.log_path)
                            return [email, "verified by smtp valid status"], results, itr
                finally:
                    try:
                        server.quit()
                    except:
                        server.close()


            unverifiable_emails = [[email, code] for email, code in results.items() if code in [421, 450, 451, 452]]
            undeliverable_emails = [[email, code] for email, code in results.items() if code in [550, 551, 553]]
            if len(unverifiable_emails) == 1 and len(undeliverable_emails) == len(results) - 1 and undeliverable_emails and unverifiable_emails:
                log(f"Email: {unverifiable_emails[0][0]} gives status_code: {unverifiable_emails} and other patterns status_code in {[551, 551, 553]} {undeliverable_emails}, for iteration {itr}", self.log_path)
                
                # RECURSIVE CALL FIX: Only retry the unverifiable one, or do not retry if max attempts reached.
                return self.smtp_batch_check(
                    emails, 
                    mx_hosts, 
                    itr + 1, 
                    results={
                        unverifiable_emails[0][0]: unverifiable_emails[0][1]
                    }
                ) if itr < MAX_ITERATIONS else (None, results, itr)

            if len(results) == len(emails) and all(code in [550, 551, 553] for code in results.values()):
                log(f"All emails in batch check returned undeliverable codes: {results} for iteration {itr}", self.log_path)
                return self.smtp_batch_check(
                    emails, 
                    mx_hosts, 
                    itr + 1
                ) if itr < MAX_ITERATIONS else (None, results, itr)

            if len(results) == len(emails) and all(code in [421, 450, 451, 452] for code in results.values()):
                log(f"All emails in batch check returned temporary failure codes: {results} for iteration {itr}", self.log_path)
                return None, results, itr

        except smtplib.SMTPServerDisconnected as e:
            log(f"SMTP server disconnected during batch check for {domain}: {e}. Retrying...Interation: {itr}, traceback: {traceback.format_exc()}", self.log_path)
            if itr < MAX_ITERATIONS:
                return self.smtp_batch_check(
                    emails,
                    mx_hosts,
                    itr + 1,
                    results,
                    stuck_check = True
                )
            else:
                log(f"Max iterations reached for email in {domain} after disconnection. Marking as unverifiable.", self.log_path)
                results[domain] = "unverifiable due to disconnection"
                return None, results, itr

        except (socket.timeout, smtplib.SMTPException, ssl.SSLError, socket.gaierror, OSError) as e:
            if isinstance(e, OSError) and e.errno == 101:
                log(f"NETWORK ERROR for {domain}: Port 25 is unreachable. AWS typically blocks Port 25 by default. Please request AWS Support to lift this restriction.", self.log_path)
                return None, results, itr
            else:
                log(f"SMTP batch error for {domain}: {e} \n\n\n Trace: {traceback.format_exc()}", self.log_path)


            if itr < MAX_ITERATIONS:
                results[domain] = f"error: {e}"
                if not isinstance(e, (socket.timeout, OSError)): # Only retry if not a timeout or network unreachable
                    return self.smtp_batch_check(
                        emails,
                        mx_hosts,
                        itr + 1,
                        results
                    )
        return None, results, itr


    # full verification pipeline
    def verify(self, emails, itr=0):

        # Ensure we check syntax before anything else
        valid_emails = self.validate_syntax(emails)
        if not valid_emails:
            log(f"email verification: {emails}, reason: bad_syntax", self.log_path)
            return {"status": "invalid", "reason": "bad_syntax"}

        domain = emails[0].split("@")[1]
        mx_records = self.get_mx_records(domain)

        if not mx_records:
            log(f"email verification: {valid_emails}, reason: domain_not_found", self.log_path)
            return {"status": "invalid", "reason": "domain_not_found"}

        valid_email, reason, itr = self.smtp_batch_check(valid_emails, mx_records, itr)

        return {
            "email": valid_email[0] if valid_email else None,
            "status": valid_email[1] if valid_email else "invalid",
            "reason": reason,
            "iteration": itr
        }
    

    def verify_via_web(self, name, domain):
        emails = self.generate_email_patterns(name, domain)
        if not emails:
            return None

        if self.cancel_event and self.cancel_event.is_set():
            return None
        log(f"Email verification through web: {domain}", self.log_path)
        valid_emails = self.validate_syntax(emails)
        if not valid_emails:
            log(f"Domain {domain}:email verification: {emails}, reason: bad_syntax", self.log_path)
            return None

        domain = emails[0].split("@")[1]
        mx_records = self.get_mx_records(domain)

        if not mx_records:
            log(f"Domain {domain}: email verification: {valid_emails}, reason: domain_not_found", self.log_path)
            return None
        
        if "ppe-hosted.com" in mx_records[0][1] or "secureserver.net" in mx_records[0][1]:
            result = self.check_godaddy_emails_selenium(valid_emails)
            if result and 'email' in result and result['email']:
                return result['email'], result['status']

        if "outlook.com" in mx_records[0][1] or "arsmtp.com" in mx_records[0][1] or \
            "barracudanetworks.com" in mx_records[0][1] or "mimecast.com" in mx_records[0][1]:
            result = self.check_outlook_emails_request(valid_emails)
            if result and 'email' in result and result['email']:
                return result['email'], result['status']

        if "google.com" in mx_records[0][1]:
            result = self.check_google_emails_selenium(valid_emails)
            if result and 'email' in result and result['email']:
                return result['email'], result['status']

        return None
    

    def get_valid_email(self, name, domain):
        result = self.verify(self.generate_email_patterns(name, domain), itr=0)
        if "email" in result and result["email"]:
            log(f"Found valid email: {result['email']} for {name} with domain {domain} in {result['iteration']} iterations", self.log_path)
            pattern = self.detect_pattern(name, result["email"])
            if pattern and ("verified by smtp valid status" in result["status"] or "verified via web browser" in result["status"]):
                with email_pattern_lock:
                    if domain not in email_pattern_for_domain:
                        email_pattern_for_domain[domain] = [pattern]
                    else:
                        email_pattern_for_domain[domain].append(pattern)
            return result["email"], result["status"]
        elif domain in email_pattern_for_domain:
            result = self.verify(self.generate_email_patterns(name, domain, use_pattern_learned=False), itr=0)
            if "email" in result and result["email"]:
                log(f"Found valid email: {result['email']} for {name} with domain {domain} in {result['iteration']} iterations with full patterns", self.log_path)
                pattern = self.detect_pattern(name, result["email"])
                if pattern and ("verified by smtp valid status" in result["status"] or "verified via web browser" in result["status"]):
                    with email_pattern_lock:
                        if domain not in email_pattern_for_domain:
                            email_pattern_for_domain[domain] = [pattern]
                        else:
                            email_pattern_for_domain[domain].append(pattern)
                return result["email"], result["status"]

        log(f"No valid email found for {name} with domain {domain}, so checking via web browser", self.log_path)
        return self.verify_via_web(name, domain)
       
    

    def check_google_emails_selenium(self, emails):
        try:
            for email in emails:
                if self.cancel_event and self.cancel_event.is_set(): return None
                valid_count = 0
                for attempt in range(1, 5):
                    if self.cancel_event and self.cancel_event.is_set(): return None
                    with selenium_chrome_driver() as driver:
                        wait = WebDriverWait(driver, 10)
                        log(f"Testing Google: {email} (Attempt {attempt}/4) - New Driver", self.log_path)
                        driver.get("https://accounts.google.com/signin/v2/identifier?flowName=GlifWebSignIn&flowEntry=ServiceLogin")
                        email_field = wait.until(EC.presence_of_element_located((By.NAME, "identifier")))
                        email_field.clear()
                        for char in email:
                            email_field.send_keys(char)
                            time.sleep(0.005)
                            
                        next_button = wait.until(EC.presence_of_element_located((By.ID, "identifierNext")))
                        next_button.click()
                        
                        time.sleep(random.uniform(2.5, 3.5)) 
                        
                        page_source = driver.page_source.lower() 
                        current_url = driver.current_url.lower()

                        if "couldn’t find your google account" in page_source or "couldn’t sign you in" in page_source:
                            log(f"{email}: RESULT: Account does NOT exist (Attempt {attempt}).", self.log_path)
                            break
                        elif "password" in page_source or "challenge" in current_url or "saml" in current_url:
                            log(f"{email}: ✅ Valid hit (Attempt {attempt}/4).", self.log_path)
                            valid_count += 1
                            break
                        elif "captcha" in page_source or "verify it’s you" in page_source:
                            log(f"{email}: ⚠️ CAPTCHA triggered (Attempt {attempt}).", self.log_path)
                            break
                        else:
                            log(f"{email}: ❓ Unknown state (Attempt {attempt}).", self.log_path)
                            break

                    time.sleep(random.uniform(2.5, 3.5)) 
                
                if valid_count == 1:
                    log(f"{email}: ✅ RESULT: Consistently VALID (Google).", self.log_path)
                    return {"email": email, "status": "verified via web browser (Google)", "reason": "verified via google mx server", "iteration": 0}

        except Exception as e:
            log(f"{emails[0].split('@')[0]}: ⚠️ Google Selenium Error: {e} {traceback.format_exc()}", self.log_path)
        
            return None


    def check_outlook_emails_request(self, emails):
        try:
            for email in emails:
                if self.cancel_event and self.cancel_event.is_set(): return None
                domain = email.split('@')[-1]

                with ms_request_semaphore:
                    log(f"Testing Microsoft Request: {email}", self.log_path)
                    
                    # 1. Check if domain is a Microsoft Tenant
                    tenant_url = f"https://login.microsoftonline.com/{domain}/v2.0/.well-known/openid-configuration"
                    tenant_res = requests.get(tenant_url, timeout=self.timeout)
                    
                    if tenant_res.status_code != 200:
                        log(f"{email}: Domain is not a Microsoft Tenant.", self.log_path)
                        break
                    
                    # 2. Check Credential Type (Username availability)
                    login_url = "https://login.microsoftonline.com/common/GetCredentialType"
                    payload = {
                        "username": email,
                        "isOtherIdpSupported": True,
                        "checkPhone": True
                    }
                    
                    cred_res = requests.post(login_url, json=payload, timeout=self.timeout).json()
                    if cred_res.get("IfExistsResult") in [0, 5, 6]:

                        if "Credentials" in cred_res and "FederationRedirectUrl" in cred_res.get("Credentials") and \
                        "sso.godaddy.com" in cred_res.get("Credentials").get("FederationRedirectUrl"):
                            log(f"{email}: Domain has Go Daddy as redirect URL. So checking through Godaddy web.", self.log_path)
                            return self.check_godaddy_emails_selenium(emails)
                        
                        if "ThrottleStatus" in cred_res and cred_res["ThrottleStatus"] == 1:
                            log(f"{email}: Reqeust throttled in the Outlook server.", self.log_path)
                            return None
                        
                        log(f"{email}: ✅ RESULT: Consistently VALID (Microsoft Request). with IfExistsResult: {cred_res.get("IfExistsResult")})", self.log_path)
                        return {"email": email, "status": "verified via web browser (Microsoft Request)", "reason": "verified via microsoft loopup server", "iteration": 0}
                    
                    else:
                        log(f"{email}: RESULT: Account does NOT exist via request. {f"Response: {cred_res}" if cred_res["IfExistsResult"] != 1 else ""}", self.log_path)
                
                # Brief delay between attempts to avoid hitting rate limits
                time.sleep(random.uniform(0.5, 1.5))
                    

        except Exception as e:
            log(f"{emails[0].split('@')[0] if emails else 'Unknown'}: ⚠️ Microsoft Request Error: {e} {traceback.format_exc()}", self.log_path)
        
        return None
    

    
    def check_microsoft_emails_selenium(self, emails):
        try:
            microsoft_login_url = "https://go.microsoft.com/fwlink/p/?LinkID=2125442&clcid=0x409&culture=en-us&country=us"
            for email in emails:
                if self.cancel_event and self.cancel_event.is_set(): return None
                valid_count = 0
                for attempt in range(1, 5):
                    if self.cancel_event and self.cancel_event.is_set(): return None
                    with selenium_chrome_driver() as driver:
                        wait = WebDriverWait(driver, 10)
                        log(f"Testing Microsoft: {email} (Attempt {attempt}/4) - New Driver", self.log_path)
                        driver.get(microsoft_login_url)
                        
                        email_field = wait.until(EC.presence_of_element_located((By.NAME, "loginfmt")))
                        email_field.clear()
                        
                        for char in email:
                            email_field.send_keys(char)
                            time.sleep(random.uniform(0.03, 0.07))
                            
                        next_button = wait.until(EC.element_to_be_clickable((By.ID, "idSIButton9")))
                        next_button.click()
                        
                        time.sleep(random.uniform(2.5, 3.5))
                        
                        error_elements = driver.find_elements(By.ID, "usernameError")
                        page_source = driver.page_source.lower()
                        
                        if error_elements and ("this username may be incorrect." in error_elements[0].text.lower() or "we couldn't find an account with that username" in error_elements[0].text.lower()):
                            log(f"{email}: RESULT: Account does NOT exist (Microsoft) (Attempt {attempt}).", self.log_path)
                            break
                        elif "enter password" in page_source or "it looks like this email is used with more than one account from microsoft. Which one do you want to use?" in page_source:
                            log(f"{email}: ✅ Valid hit (Attempt {attempt}/4).", self.log_path)
                            valid_count += 1
                        elif "captcha" in page_source or "type the characters" in page_source:
                            log(f"{email}: ⚠️ CAPTCHA triggered (Attempt {attempt}).", self.log_path)
                            break
                        else:
                            log(f"{email}: ❓ Unknown state (Attempt {attempt}).", self.log_path)
                            break
                
                if valid_count == 4:
                    log(f"{email}: ✅ RESULT: Consistently VALID (Microsoft).", self.log_path)
                    return {"email": email, "status": "verified via web browser (Microsoft)", "reason": "verified via microsoft loopup server", "iteration": 0}

        except Exception as e:
            log(f"{emails[0].split('@')[0]}: ⚠️ Microsoft Selenium Error: {e} {traceback.format_exc()}", self.log_path)
        return None


    def check_godaddy_emails_selenium(self, emails):
        try:
            godaddy_reset_url = "https://sso.godaddy.com/account/reset?app=o365&realm=pass"
            for email in emails:
                if self.cancel_event and self.cancel_event.is_set(): return None
                valid_count = 0
                attempt = 1
                if self.cancel_event and self.cancel_event.is_set(): return None
                with selenium_chrome_driver() as driver:
                    wait = WebDriverWait(driver, 10)
                    log(f"Testing GoDaddy: {email} (Attempt {attempt}/4) - New Driver", self.log_path)
                    driver.get(godaddy_reset_url)
                    
                    email_field = wait.until(EC.presence_of_element_located((By.ID, "PasswordRecoveryLandingUsername")))
                    email_field.clear()
                    
                    for char in email:
                        email_field.send_keys(char)
                        time.sleep(random.uniform(0.03, 0.07))
                        
                    next_button = wait.until(EC.element_to_be_clickable((By.ID, "PasswordRecoveryLandingSubmitButton")))
                    next_button.click()
                    
                    time.sleep(random.uniform(2.5, 3.5))
                    
                    page_source = driver.page_source.lower()
                    
                    if "that email address doesn’t exist in our system" in page_source:
                        log(f"{email}: RESULT: Account does NOT exist (GoDaddy) (Attempt {attempt}).", self.log_path)
                    elif "how do you want to reset your password?" in page_source: 
                        log(f"{email}: ✅ Valid hit (Attempt {attempt}/4).", self.log_path)
                        valid_count += 1
                    elif "captcha" in page_source or "verify it's you" in page_source:
                        log(f"{email}: ⚠️ CAPTCHA triggered (Attempt {attempt}).", self.log_path)
                        break
                    else:
                        log(f"{email}: ❓ Unknown state (Attempt {attempt}).", self.log_path)

                if valid_count == 1:
                    log(f"{email}: ✅ RESULT: Consistently VALID (GoDaddy).", self.log_path)
                    return {"email": email, "status": "verified via web browser (GoDaddy)", "reason": "verified via godaddy lookup server", "iteration": 0}

        except Exception as e:
            log(f"{emails[0].split('@')[0]}: ⚠️ GoDaddy Selenium Error: {e} {traceback.format_exc()}", self.log_path)
        
        return None


class EmailFakeChecker:

    def __init__(self, log_path=None, cancel_event=None, extra_disposable=None):
        self.log_path = log_path
        self.cancel_event = cancel_event
        self.disposable_domains = DISPOSABLE_DOMAINS.copy()
        if extra_disposable and isinstance(extra_disposable, list):
            self.disposable_domains.update(extra_disposable)


    # disposable domains example
    def random_email(self, domain):
        name = ''.join(random.choices(string.ascii_lowercase, k=10))
        return f"{["johnnm", "tomjerry"][random.randint(0, 1)]}@{domain}"


    def verify_email(self, domain):

        log(f"Checking: {domain}", self.log_path)

        # 2 disposable
        if domain in self.disposable_domains:
            log(f"Disposable email domain: {domain}", self.log_path)
            return False, "Disposable email domain"

        # 3 MX check
        mx_records = EmailVerifier(self.log_path, cancel_event=self.cancel_event).get_mx_records(domain)
        if not mx_records:
            log(f"No MX records found for {domain}", self.log_path)
            return False, "No MX records found"

        log(f"MX server: {mx_records}", self.log_path)

        # 4 SMTP verification
        # for _ in range(2):
        #     if self.cancel_event and self.cancel_event.is_set(): return False, "Cancelled"
        #     result = EmailVerifier(self.log_path, cancel_event=self.cancel_event).verify([self.random_email(domain)], itr=0)
        #     log(f"Fake Email Check for {domain}: {result}", self.log_path)
        #     # {'uookmxtqtr@aspelllaw.com': 550}
        #     if result["status"] != "invalid" or "reason" in result and \
        #         len(result["reason"]) == len([reason for reason in result["reason"].values() 
        #                                       if isinstance(reason, str) and "error" in reason]):
        #         log(f"Fake Email Check for {domain}: failed", self.log_path)
        #         return False, result
        # log(f"Fake Email Check for {domain}: Passed", self.log_path)
        return True, f"MX server ({domain}): {mx_records}: Passed"
        


# log(EmailVerifier().verify(["thefoundersorbit@gmail.com"]))
# log(EmailVerifier().verify(["thefounderorbit@gmail.com"]))

# tgreenwell@brandonjbroderick.com
# log(EmailFakeChecker().verify_email("cbhplaw.com"))
# chaffinluhana.com
# log(EmailVerifier().verify(["abc@chaffinluhana.com"], itr=0))
# log(EmailVerifier().verify(EmailVerifier().generate_email_patterns("James a armentano", "katzandseligman.com"), itr=0))