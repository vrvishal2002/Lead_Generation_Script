import os, csv, pandas as pd
import time
from urllib.parse import urlparse
from firm_parser import FirmParser
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


def firm_worker():
    while True:
        firm = firm_queue.get()

        try:
            
            # Checking for Fake email acceptance..
            log(f"Checking for Fake email acceptance for firm {firm['Firm Name']}...", log_path)
            check_fake = EmailFakeChecker(log_path=log_path).verify_email(get_domain(firm["Website"]))
            if not check_fake[0]:
                log(f"domain {get_domain(firm['Website'])} is accepting fake emails so stop processing for current firm", log_path)
                continue

            profiles = FirmScraper(log_path=log_path).scrape_firm(firm["Website"])
            log(f"Found {len(profiles)} Profiles for {firm['Firm Name']}", log_path)

            for profile in profiles:
                profile["Firm Name"] = firm["Firm Name"]
                profile_queue.put(profile)

        except Exception as e:
            log(f"Firm error {firm['Firm Name']} {e}", log_path)

        finally:
            firm_queue.task_done()


def email_worker(city="", state="", profile_names=set()):
    while True:
        profile = profile_queue.get()
        if "Name" in profile and profile["Name"] in profile_names:
            log(f"Profile {profile['Name']} already exists. Skipping email verification.", log_path)
            profile_queue.task_done()
            continue
        try:
            email = ""
            log(f"profile: {profile}, 'fsio'", log_path)
            if "Email" in profile and profile["Email"]:
                email = profile["Email"].strip()
            log(f"profile: {profile}, 'fsio'", log_path)
            if email and not name_processor_lib.is_valid_attorney_slug(email.split('@')[0]):
                email = ""
            log(f"profile: {profile}, 'fsio'", log_path)
            if email:
                verified = EmailVerifier(log_path=log_path).verify(emails=[email])
                if verified["status"] != "valid":
                    email = ""
            log(f"profile: {profile}, 'fsio'", log_path)
            if not email:
                email = EmailVerifier(log_path=log_path).get_valid_email(
                    profile["Name"],
                    get_domain(profile["Profile URL"])
                )
            log(f"profile: {profile}, 'fsio'", log_path)
            if email:
                profile["Email"] = email
                profile["City"] = city
                profile["State"] = state
                result_queue.put(profile)

        except Exception as e:
            log(f"{profile.get('Name', 'Unknown')}: Email worker error {e}", log_path)

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