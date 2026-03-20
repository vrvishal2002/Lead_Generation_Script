import os, csv, pandas as pd
import time
from urllib.parse import urlparse
from firm_scraper import FirmScraper
from email_verifier import EmailVerifier, EmailFakeChecker
from log_lib import get_log_name, log
import name_processor_lib
from queue import Queue

firm_queue = Queue(maxsize=1000)
profile_queue = Queue(maxsize=1000)
result_queue = Queue(maxsize=1000)
log_path = get_log_name()
profile_file_name = "attorney_profiles/attorney_profiles_final.csv"
name_processor_lib.log_path = log_path


def get_attorney_file_name(state="", city=""):
    return f"{profile_file_name.split('.')[0]}_{city}_{state}.csv"


def get_domain(url):
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    return domain


def firm_worker(existing_domain_names=None, domains_with_no_leads=None):
    existing_domain_names = existing_domain_names or set()
    domains_with_no_leads = domains_with_no_leads or set()
    fake_checker = EmailFakeChecker(log_path=log_path)
    scraper = FirmScraper(log_path=log_path)

    while True:
        firm = firm_queue.get()
        website = firm.get("Website", "")
        firm_name = firm.get("Firm Name", "Unknown Firm")
        domain = get_domain(website)

        try:
            if not website or not domain:
                log(f"Firm {firm_name} has no valid website. Skipping.", log_path)
                continue

            if domain in existing_domain_names:
                log(f"Already got leads from domain {domain}. Skipping firm {firm_name}.", log_path)
                continue

            if domain in domains_with_no_leads:
                log(f"Domain {domain} was previously found to have no profiles with valid emails. Skipping firm {firm_name}.", log_path)
                continue

            log(f"Checking fake email acceptance for firm {firm_name}...", log_path)
            check_fake = fake_checker.verify_email(domain)
            if not check_fake[0]:
                log(f"Domain {domain} is accepting fake emails, skipping firm {firm_name}.", log_path)
                continue

            profiles = scraper.scrape_firm(website)
            log(f"Found {len(profiles)} profiles for {firm_name}", log_path)

            for profile in profiles:
                profile["Firm Name"] = firm_name
                profile_queue.put(profile)

        except Exception as e:
            log(f"Firm error {firm_name} {e}", log_path)

        finally:
            firm_queue.task_done()


def email_worker(city="", state="", profile_names=None, domain_with_no_profiles_cache=None, domain_with_no_profiles_cache_lock=None):
    profile_names = profile_names or set()
    verifier = EmailVerifier(log_path=log_path)

    while True:
        profile = profile_queue.get()
        profile_name = profile.get("Name", "Unknown")
        profile_domain = get_domain(profile.get("Profile URL", ""))

        try:
            if domain_with_no_profiles_cache is not None and domain_with_no_profiles_cache_lock is not None and profile_domain:
                with domain_with_no_profiles_cache_lock:
                    domain_with_no_profiles_cache.setdefault(profile_domain, True)

            if profile_name in profile_names:
                log(f"Profile {profile_name} already exists. Skipping email verification.", log_path)
                continue

            email = (profile.get("Email") or "").strip()

            if email and not name_processor_lib.is_valid_attorney_slug(email.split("@")[0]):
                email = ""

            if email:
                verified = verifier.verify(emails=[email])
                if verified["status"] == "invalid":
                    email = ""

            if not email and profile_name and profile_domain:
                email = verifier.get_valid_email(profile_name, profile_domain)

            if email:
                profile["Email"] = email
                profile["City"] = city
                profile["State"] = state
                result_queue.put(profile)

                if domain_with_no_profiles_cache is not None and domain_with_no_profiles_cache_lock is not None and profile_domain:
                    with domain_with_no_profiles_cache_lock:
                        domain_with_no_profiles_cache[profile_domain] = False

        except Exception as e:
            log(f"{profile_name}: Email worker error {e}", log_path)

        finally:
            profile_queue.task_done()


def csv_writer():
    while True:
        profile = result_queue.get()

        try:
            df = pd.DataFrame([profile])

            df.to_csv(
                get_attorney_file_name(state=profile.get("State", ""), city=profile.get("City", "")),
                mode="a",
                header=False,
                index=False
            )

        except Exception as e:
            log(f"CSV write error {e}", log_path)

        finally:
            result_queue.task_done()


def monitor():
    while True:
        log(
            f"Queues → firm:{firm_queue.qsize()} profile:{profile_queue.qsize()} result:{result_queue.qsize()}",
            log_path
        )
        time.sleep(5)
