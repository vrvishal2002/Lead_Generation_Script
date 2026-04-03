import os, csv, pandas as pd, io
import traceback
import threading
import time
from urllib.parse import urlparse
import boto3
from firm_scraper import FirmScraper
from email_verifier import EmailVerifier, EmailFakeChecker
from log_lib import get_log_name, log, csv_file_lock
import name_processor_lib
from queue import Queue

log_path = get_log_name()
profile_file_name = "attorney_profiles_final.csv"
name_processor_lib.log_path = log_path

S3_BUCKET = os.environ.get("S3_BUCKET_NAME")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
s3_client = boto3.client('s3', region_name=AWS_REGION) if S3_BUCKET else None

class LeadGenerationHelper:

    def __init__(self, city="", state=""):
        self.log_path = log_path
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



    @staticmethod
    def get_attorney_file_name(state="", city=""):
        if state:
            path = f"attorney_profiles/{state}"
            if not S3_BUCKET and not os.path.exists(path):
                os.makedirs(f"attorney_profiles/{state}")
                print(f"Created directory: attorney_profiles/{state}")
            return f"attorney_profiles/{state}/{profile_file_name.split('.')[0]}_{city}_{state}.csv"
        return f"attorney_profiles/{profile_file_name.split('.')[0]}_{city}_{state}.csv"


    @staticmethod
    def get_domain(url):
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")
        return domain


    def firm_worker(self):
        fake_checker = EmailFakeChecker(log_path=log_path)
        scraper = FirmScraper(log_path=log_path)

        while True:
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

                profiles = scraper.scrape_firm(website)
                log(f"Found {len(profiles)} profiles for {firm_name}", log_path)

                for profile in profiles:
                    profile["Firm Name"] = firm_name
                    if "accept_fake" not in firm:
                        profile["accept_fake"] = False
                    else: 
                        profile["accept_fake"] = True
                    
                    self.profile_queue.put(profile)

            except Exception as e:
                error_trace = traceback.format_exc()
                log(f"Firm error {firm_name} {e}", log_path)
                log(f"Error trace for firm {firm_name}: {error_trace}", log_path)

            finally:
                self.firm_queue.task_done()


    def email_worker(self):
        verifier = EmailVerifier(log_path=log_path)

        while True:
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
                    if profile['accept_fake']:
                        email_result = verifier.verify_via_web(profile_name, profile_domain)
                    else:
                        email_result = verifier.get_valid_email(profile_name, profile_domain)
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
                file_path = self.get_attorney_file_name(state=profile.get("State", ""), city=profile.get("City", ""))
                df = pd.DataFrame([profile])

                if S3_BUCKET:
                    # S3 Append Simulation: Read existing, concatenate, upload
                    existing_df = pd.DataFrame()
                    try:
                        obj = s3_client.get_object(Bucket=S3_BUCKET, Key=file_path)
                        content = obj['Body'].read()
                        if content and len(content.strip()) > 0:
                            existing_df = pd.read_csv(io.BytesIO(content))
                    except (s3_client.exceptions.NoSuchKey, pd.errors.EmptyDataError):
                        pass
                    
                    final_df = pd.concat([existing_df, df], ignore_index=True)
                    csv_buf = io.StringIO()
                    final_df.to_csv(csv_buf, index=False)
                    s3_client.put_object(Bucket=S3_BUCKET, Key=file_path, Body=csv_buf.getvalue().encode('utf-8'))
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
            # Check if we should stop
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
