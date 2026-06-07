import os, pandas as pd, io
import traceback
import threading
import time
from urllib.parse import urlparse
import storage_lib
from firm_scraper import FirmScraper
from email_verifier import EmailVerifier, EmailFakeChecker
from log_lib import get_log_name, log, csv_file_lock
import name_processor_lib
from queue import Queue


log_path = get_log_name()
name_processor_lib.log_path = log_path

class LeadGenerationHelper:

    def __init__(self, city="", state="", config=None):
        self.log_path = log_path
        self.config = config or {
            "id": "attorney",
            "name": "Attorney Leads",
            "folder_name": "attorney_profiles",
            "file_prefix": "attorney",
            "default_query": "medical malpractice and personal injury lawyers in",
            "directory_keywords": [
                "attorneys", "our-team", "team", "legal-team", "lawyers", "meet",
                "profiles", "about", "people", "paralegals", "advocates"
            ],
            "profile_required_keywords": [
                "personal injury", "medical malpractice", "medical negligence",
                "wrongful death", "accident", "dog bite", "drug"
            ],
            "lead_role_keywords": [
                "associate", "attorney", "advocate", "lawyer", "partner",
                "paralegal", "legal", "esq", "of counsel", "senior", "principal"
            ],
            "slug_exclusion_keywords": [
                "attorney", "lawyer", "profile", "legal", "contact",
                "disclaimer", "privacy", "blog", "news"
            ],
            "disposable_exclusion": [
                "mailinator.com", "tempmail.com", "10minutemail.com", "guerrillamail.com",
                "protectingpatientrights.com", "cellinolaw.com", "cartermario.com",
                "brandonjbroderick.com", "carmodylaw.com", "danaherlagnese.com",
                "brownandcrouppen.com", "devaultlaw.com", "www.devaultlaw.com"
            ]
        }
        self.gathered_domain_names = set()
        self.gathered_profile_names = set()
        self.domains_with_no_leads = set()
        self.city = city
        self.state = state
        self.firm_queue = Queue(maxsize=1000)
        self.profile_queue = Queue(maxsize=1000)
        self.result_queue = Queue(maxsize=1000)
        self.monitor_queue = Queue(maxsize=1)
        self.domain_with_no_profiles_cache = {}
        self.domain_with_no_profiles_cache_lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.threads = []
        self.status = {
            "phase": "Starting",
            "current_city": city,
            "total_cities": 0,
            "cities_completed": 0,
            "firms_discovered": 0,
            "firms_processed": 0,
            "firms_total": 0,
            "firms_target": 0,
            "profiles_found": 0,
            "recent_leads": [],
            "overall_progress": 0,
            "cities": {}
        }


    def get_file_name(self, state="", city=""):
        folder = self.config.get("folder_name", "attorney_profiles")
        prefix = self.config.get("file_prefix", "attorney")
        if state:
            # In local mode, ensure the state sub-directory exists
            if not storage_lib.is_cloud():
                os.makedirs(f"{folder}/{state}", exist_ok=True)
            return f"{folder}/{state}/{prefix}_profiles_{city}_{state}.csv"
        return f"{folder}/{prefix}_profiles_{city}_{state}.csv"


    @staticmethod
    def get_domain(url):
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")
        return domain


    def firm_worker(self):
        extra_exclusion = self.config.get("disposable_exclusion", [])
        fake_checker = EmailFakeChecker(log_path=log_path, cancel_event=self.cancel_event, extra_disposable=extra_exclusion)
        scraper = FirmScraper(log_path=self.log_path, config=self.config)

        while not self.cancel_event.is_set():
            firm = self.firm_queue.get()
            if firm is None:
                self.firm_queue.task_done()
                break

            website = firm.get("Website", "")
            firm_name = firm.get("Firm Name", "Unknown Firm")
            domain = self.get_domain(website)

            try:
                if not website or not domain:
                    log(f"Firm {firm_name} has no valid website. Skipping.", log_path)
                    continue

                if domain in self.gathered_domain_names:
                    log(f"Already got leads from domain {domain}. Skipping firm {firm_name}.", log_path)
                    continue

                if domain in self.domains_with_no_leads:
                    log(f"Domain {domain} was previously found to have no profiles with valid emails. Skipping firm {firm_name}.", log_path)
                    continue

                log(f"Checking fake email acceptance for firm {firm_name}...", log_path)
                check_fake = fake_checker.verify_email(domain)
                if not check_fake[0]:
                    log(f"Domain {domain} is accepting fake emails, So will check through web for {firm_name}.", log_path)
                    firm["accept_fake"] = True

                profiles = scraper.scrape_firm(website, firm_name=firm_name)
                log(f"Found {len(profiles)} profiles for {firm_name}", log_path)

                for profile in profiles:
                    profile["Firm Name"] = firm_name
                    profile["accept_fake"] = firm.get("accept_fake", False)
                    self.profile_queue.put(profile)

                self.status["firms_processed"] += 1

            except Exception as e:
                error_trace = traceback.format_exc()
                log(f"Firm error {firm_name} {e}", log_path)
                log(f"Error trace for firm {firm_name}: {error_trace}", log_path)

            finally:
                self.firm_queue.task_done()


    def email_worker(self):
        verifier = EmailVerifier(log_path=log_path, cancel_event=self.cancel_event)

        while not self.cancel_event.is_set():
            profile = self.profile_queue.get()
            if profile is None:
                self.profile_queue.task_done()
                break

            profile_name = profile.get("Name", "Unknown")
            profile_domain = self.get_domain(profile.get("Profile URL", ""))

            try:
                if self.domain_with_no_profiles_cache is not None and self.domain_with_no_profiles_cache_lock is not None and profile_domain:
                    with self.domain_with_no_profiles_cache_lock:
                        self.domain_with_no_profiles_cache.setdefault(profile_domain, True)

                if profile_name in self.gathered_profile_names:
                    log(f"Profile {profile_name} already exists. Skipping email verification.", log_path)
                    continue

                email = (profile.get("Email") or "").strip()

                status = "From the website"
                if email and not name_processor_lib.is_strong_name_word(email.split("@")[0]):
                    log(f"Email {email} for profile {profile_name} does not have a valid slug. Discarding email.", log_path)
                    email = ""
                if email and not verifier.detect_pattern(profile_name, email):
                    email_alpha_list = [char for char in email.split("@")[0].lower() if char.isalpha()]
                    profile_alpha_list = [char for char in profile_name.lower() if char.isalpha()]
                    for char in email_alpha_list:
                        if char not in profile_alpha_list:
                            log(f"Email {email} for profile {profile_name} does not match name pattern. Discarding email.", log_path)
                            email = ""
                            break

                if not email and profile_name and profile_domain:
                    email_result = verifier.verify_via_web(profile_name, profile_domain)
                    if email_result:
                        email = email_result[0]
                        status = email_result[1]

                if email:
                    del profile["accept_fake"]
                    self.gathered_profile_names.add(profile_name)
                    profile["Email"] = email
                    profile["City"] = self.city
                    profile["State"] = self.state
                    profile["Verified By"] = status
                    self.result_queue.put(profile)
                    self.status["profiles_found"] += 1

                    self.status["recent_leads"].append({
                        "Name": profile_name,
                        "Email": email,
                        "Firm": profile.get("Firm Name", ""),
                        "Phone": profile.get("Phone", ""),
                        "City": self.city,
                        "State": self.state,
                        "URL": profile.get("Profile URL", ""),
                        "VerifiedBy": status
                    })

                    if self.domain_with_no_profiles_cache is not None and self.domain_with_no_profiles_cache_lock is not None and profile_domain:
                        with self.domain_with_no_profiles_cache_lock:
                            self.domain_with_no_profiles_cache[profile_domain] = False

            except Exception as e:
                log(f"{profile_name}: Email worker error {e}\n traceback{traceback.format_exc()}", log_path)

            finally:
                self.profile_queue.task_done()


    def csv_writer(self):
        while True:
            profile = self.result_queue.get()

            try:
                if profile is None:
                    break

                file_path = self.get_file_name(state=profile.get("State", ""), city=profile.get("City", ""))
                df = pd.DataFrame([profile])

                if storage_lib.is_cloud():
                    # Read-modify-write so concurrent workers don't clobber each other
                    existing_df = pd.DataFrame()
                    try:
                        content = storage_lib.read_file(file_path)
                        if content and len(content.strip()) > 0:
                            existing_df = pd.read_csv(io.BytesIO(content))
                            existing_df = existing_df.dropna(how="all")
                    except FileNotFoundError:
                        pass
                    except Exception as read_err:
                        log(f"csv_writer: could not read existing file {file_path} (starting fresh): {read_err}", log_path)

                    final_df = pd.concat([existing_df, df], ignore_index=True)
                    csv_buf = io.StringIO()
                    final_df.to_csv(csv_buf, index=False)
                    storage_lib.write_file(file_path, csv_buf.getvalue())
                else:
                    with csv_file_lock:
                        header = not os.path.exists(file_path)
                        df.to_csv(file_path, mode="a", header=header, index=False)

            except Exception as e:
                log(f"CSV write error {e}", log_path)

            finally:
                self.result_queue.task_done()


    def monitor(self):
        while True:
            if not self.monitor_queue.empty():
                val = self.monitor_queue.get()
                if val is None:
                    self.monitor_queue.task_done()
                    break

            log(
                f"Queues → firm:{self.firm_queue.qsize()} profile:{self.profile_queue.qsize()} result:{self.result_queue.qsize()}",
                log_path
            )
            time.sleep(5)
