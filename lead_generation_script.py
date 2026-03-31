import os, csv, pandas as pd
from urllib.parse import urlparse
from firm_parser import FirmParser
from firm_scraper import FirmScraper
from email_verifier import EmailVerifier, EmailFakeChecker
from log_lib import get_log_name, log
import name_processor_lib


log_path = get_log_name()
name_processor_lib.log_path = log_path


def get_domain(url):
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    return domain


from concurrent.futures import ThreadPoolExecutor
import threading

profiles_csv_lock = threading.Lock()

def process_firm(firm):
    log("", log_path)
    log(firm, log_path)

    profiles = FirmScraper(log_path=log_path).scrape_firm(firm["Website"])
    log(f"Found {len(profiles)} Profiles for {firm['Firm Name']}", log_path)

    count_removed = 0
    count_added = 0
    total_profiles = len(profiles)

    for profile in profiles.copy():
        attorney_email = ''
        log(f"{firm['Firm Name']}: {profile}", log_path)
        if profile['Email'] != '' and name_processor_lib.is_strong_name_word(profile['Email'].split('@')[0]) and \
            EmailVerifier(log_path=log_path).verify(profile["Email"])["status"] == "valid":
            attorney_email = profile["Email"]
        else:
            attorney_email, itr = EmailVerifier(log_path=log_path).get_valid_email(profile['Name'], get_domain(profile['Profile URL']))
            if attorney_email:
                log(f"Found valid email after {itr} iterations: {attorney_email}", log_path)
        if attorney_email:
            log("Checking for Fake email...", log_path)
            check_fake = EmailFakeChecker(log_path=log_path).verify_email(attorney_email)
            if not check_fake[0]:
                attorney_email = ''
                log(f"domain {get_domain(profile["Profile URL"])} is accepting fake emails so stop processing for current firm", log_path)
                break
            
        profile["Email"] = attorney_email
        log(f"{firm['Firm Name']}: {profile}", log_path)
        if not attorney_email:
            log(f"Removed {profile['Name']} from attorney_profiles_final.csv", log_path)
            count_removed += 1
            if (count_removed == 10 or count_removed >= total_profiles // 2 and count_removed > 5) and count_added == 0:
                log(f"Removed {count_removed} profiles from {total_profiles} profiles and total added is {count_added}, so stop processing for {firm} firm", log_path)
                break
            continue

        count_added += 1
        profile["Firm Name"] = firm["Firm Name"]

        df = pd.DataFrame([profile])
        with profiles_csv_lock:
            df.to_csv(
                profile_file_name,
                mode="a",                 # Append mode
                header=not os.path.isfile(profile_file_name),   # Write header only if file doesn't exist
                index=False
            )
            log(f"Added {profile['Name']} ({attorney_email}) to attorney_profiles_final.csv", log_path)

    log(f"Completed processing {firm['Firm Name']} firm Attorneys", log_path)



if __name__ == "__main__":
    query = input("Enter query (example: medical malpractice lawyers New York): ")
    target = int(input("How many firms?: "))

    firms = FirmParser(log_path=log_path).scrape_google_places(query, target)
    log(firms, log_path)
    df = pd.DataFrame(firms)

    df.to_csv("google_places_firms.csv", index=False)


    # -----------TESTING-------------------
    # with open("google_places_firms.csv", newline='', encoding='utf-8') as f:
    #     reader = csv.DictReader(f)
    #     firms = list(reader)
    #--------------------------------------


    profile_file_name = "attorney_profiles_final.csv"

    if os.path.exists(profile_file_name):
        os.remove(profile_file_name)
        log("Previous Attorney File deleted successfully.", log_path)

    # Define your column names
    headers = ['Name', 'Phone', 'Email', 'Profile URL']

    with open(profile_file_name, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(headers)


    log(firms, log_path)
    # firms = [{
    #     "Firm Name": "Warhawk Legal",
    #     "Website": "https://www.warhawklegal.com"
    # }]
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(process_firm, firm) for firm in firms]
        for future in futures:
            future.result()

        




        


