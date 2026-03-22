import re, string, random
from collections import Counter
import smtplib
import time
import dns.resolver
import threading
import socket, ssl
import soup_content_lib
from log_lib import log


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
        "danaherlagnese.com"
}

mx_cache = {}
user_domain_lock = threading.Lock()
mx_cache_lock = threading.Lock()
domain_locks = {}
domain_locks_lock = threading.Lock()
email_pattern_for_domain = {}
email_pattern_lock = threading.Lock()


class EmailVerifier:
    

    def __init__(self, log_path=None):
        self.timeout = 10
        self.log_path = log_path


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



    def generate_email_patterns(self, full_name, domain):
        domain = self.clean_domain(domain)
        pattern=None
        if domain in email_pattern_for_domain:
            pattern = email_pattern_for_domain[domain]

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

        if pattern and pattern in mapping and not full_middle and not middle_initials:
            return [f"{mapping[pattern]}"]
        
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
            }
            if pattern and pattern in middle_part_pattern:
                return [f"{middle_part_pattern[pattern]}"]
            mapping.update(middle_part_pattern)

        

        # Full middle
        if full_middle:
            full_middle_part_pattern = {
                "{first}.{full_middle}.{last}@{domain}":f"{first}.{full_middle}.{last}@{domain}",
                "{first}{full_middle}{last}@{domain}":f"{first}{full_middle}{last}@{domain}",
            }
            if pattern and pattern in full_middle_part_pattern:
                return [f"{full_middle_part_pattern[pattern]}"]
            mapping.update(full_middle_part_pattern)

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

                with smtplib.SMTP(mx, 25, timeout=self.timeout) as server:
                    USER_DOMAIN = random.choice(VALIDATING_USER_DOMAINS)
                    server.ehlo(USER_DOMAIN)

                    if server.has_extn("STARTTLS"):
                        server.starttls()
                        server.ehlo(USER_DOMAIN)

                    sender = f"admin@{[domain, USER_DOMAIN][itr % 2]}"

                    server.mail(sender)

                    for email in remaining:

                        code, message = server.rcpt(email)
                        results[email] = code

                        if code in [250, 251]:
                            log(f"Email: {email} verified with code: {code} in iteration {itr}", self.log_path)
                            return [email, "verified by smtp valid status"], results, itr


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

        except smtplib.SMTPServerDisconnected:
            log(f"SMTP server disconnected during batch check for {domain}. Retrying...Interation: {itr}", self.log_path)
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

        except (socket.timeout, smtplib.SMTPException, ssl.SSLError, socket.gaierror) as e:
            log(f"SMTP batch error for {domain}: {e}", self.log_path)
            if itr < MAX_ITERATIONS:
                results[domain] = f"error: {e}"
                if not isinstance(e, socket.timeout):
                    return self.smtp_batch_check(
                        emails,
                        mx_hosts,
                        itr + 1,
                        results
                    )
        return None, results, itr


    # full verification pipeline
    def verify(self, emails, itr=0):

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
    
    

    def get_valid_email(self, name, domain):
        result = self.verify(self.generate_email_patterns(name, domain), itr=0)
        if "email" in result and result["email"]:
            log(f"Found valid email: {result['email']} for {name} with domain {domain} in {result['iteration']} iterations", self.log_path)
            pattern = self.detect_pattern(name, result["email"])
            if pattern and result["status"] in ("verified by smtp valid status", "verified via web browser"):
                with email_pattern_lock:
                    email_pattern_for_domain[domain] = pattern
            return result["email"]
        log(f"No valid email found for {name} with domain {domain}", self.log_path)
        return None    
    


class EmailFakeChecker:

    def __init__(self, log_path=None):
        self.log_path = log_path


    # disposable domains example
    def random_email(self, domain):
        name = ''.join(random.choices(string.ascii_lowercase, k=10))
        return f"{name}@{domain}"


    def verify_email(self, domain):

        log(f"Checking: {domain}", self.log_path)

        # 2 disposable
        if domain in DISPOSABLE_DOMAINS:
            log(f"Disposable email domain: {domain}", self.log_path)
            return False, "Disposable email domain"

        # 3 MX check
        mx_records = EmailVerifier(self.log_path).get_mx_records(domain)
        if not mx_records:
            log(f"No MX records found for {domain}", self.log_path)
            return False, "No MX records found"

        log(f"MX server: {mx_records}", self.log_path)

        # 4 SMTP verification
        for _ in range(2):
            result = EmailVerifier(self.log_path).verify([self.random_email(domain)], itr=0)
            log(f"Fake Email Check for {domain}: {result}", self.log_path)
            # {'uookmxtqtr@aspelllaw.com': 550}
            if result["status"] != "invalid" or "reason" in result and \
                len(result["reason"]) == len([reason for reason in result["reason"].values() 
                                              if isinstance(reason, str) and "error" in reason]):
                log(f"Fake Email Check for {domain}: failed", self.log_path)
                return False, result
        log(f"Fake Email Check for {domain}: Passed", self.log_path)
        return True, f"Fake Email Check for {domain}: Passed"
        


# log(EmailVerifier().verify(["thefoundersorbit@gmail.com"]))
# log(EmailVerifier().verify(["thefounderorbit@gmail.com"]))

# tgreenwell@brandonjbroderick.com
# print(EmailFakeChecker().verify_email("cbhplaw.com"))
# chaffinluhana.com
# log(EmailVerifier().verify(["abc@chaffinluhana.com"], itr=0))
# print(EmailVerifier().verify(EmailVerifier().generate_email_patterns("James a armentano", "katzandseligman.com"), itr=0))