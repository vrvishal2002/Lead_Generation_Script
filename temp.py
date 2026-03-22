# import csv, os

# file_path = "attorney_profiles/attorney_profiles_final_New Haven_Connecticut.csv"

# data = []

# folder_path = "attorney_profiles"

# all_data = []

# for file_name in os.listdir(folder_path):
#     if file_name.endswith(".csv"):
#         file_path = os.path.join(folder_path, file_name)
        
#         print(f"Reading: {file_name}")
        
#         with open(file_path, encoding="utf-8", errors="ignore") as f:
#             reader = csv.reader(f)
            
#             for row in reader:
#                 data.append(row)

# # Print result
# email_set = set()
# consolidated_data = []
# for row in data:
#     if row[2] not in email_set:  # Assuming email is in the third column (index 2)
#         consolidated_data.append(row)
#     else:
#         print(f"Duplicate email found and skipped: {row[2]}")  # Optional: log duplicate emails
#     email_set.add(row[2])  # Assuming email is in the third column (index 2)

# header = ["Name", "Phone", "Email", "Profile URL", "Company", "City", "State"]

# with open("attorney_profiles_final_consolidated.csv", mode="w", encoding="utf-8", newline='') as file:
#     writer = csv.writer(file)
    
#     # Write header first
#     writer.writerow(header)
    
#     # Write existing rows
#     for row in consolidated_data:
#         writer.writerow(row)
    



from urllib.parse import urljoin, urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from email_verifier import EmailFakeChecker
import name_processor_lib
from bs4 import BeautifulSoup
from log_lib import log, get_log_name
import soup_content_lib

# chrome_options = Options()
# chrome_options.add_argument("--no-sandbox")
# chrome_options.add_argument("--disable-dev-shm-usage")

# # Replace <EC2_PUBLIC_IP> with your EC2 public IP, NO trailing slash
# driver = webdriver.Remote(
#     command_executor='http://13.235.23.148:4444',  
#     options=chrome_options
# )

log_path = get_log_name()
DIRECTORY_KEYWORDS = [
        "attorneys",
        "our-team",
        "team",
        "legal-team",
        "lawyers",
        "meet",
        "profiles",
        "about",
        "people"
    ]









# from directory_processing_helper import DirectoryProcessingHelper
from firm_scraper import FirmScraper

# log(f"{FirmScraper(log_path=log_path).scrape_firm('https://www.reardonlaw.com')}", log_path)

# log("Collecting directory candidates...", log_path)
# candidates = DirectoryProcessingHelper(log_path=log_path).collect_directory_candidates("https://www.connecticutinjuryhelp.com/")

# home_url = "https://www.connecticutinjuryhelp.com/"

# log("\nPossible directory URLs:", log_path)
# for c in candidates:
#     log(c, log_path)

# directory_url = DirectoryProcessingHelper(log_path=log_path).select_best_directory(candidates, home_url)
# print(f"Selected Directory: {directory_url}")

# print(score_directory_candidate("https://mdmlawyer.com/people", "https://mdmlawyer.com/"))
# driver.get("https://mdmlawyer.com/")
# log(BeautifulSoup(driver.page_source, "html.parser"), get_log_name())

# driver.quit()
EmailFakeChecker(log_path=log_path).verify_email("reardonlaw.com")


