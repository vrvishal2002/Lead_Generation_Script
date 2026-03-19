import os
from threading import Thread
import pandas as pd
import csv
import lead_generation_helper
from log_lib import get_log_name, log, get_lead_profile_names
from lead_generation_helper import csv_writer, firm_queue, profile_queue, result_queue, firm_worker, email_worker, monitor
from firm_parser import FirmParser
import faulthandler
import sys


# --------------- For Windows to handle potential freezes ----------------
# faulthandler.enable(file=sys.stderr)
# faulthandler.dump_traceback_later(60, repeat=True)
#-------------------------------------------------------------------------


log_path = get_log_name()
lead_generation_helper.log_path = log_path
profile_file_name = "attorney_profiles_final.csv"

if __name__ == "__main__":
    state = "Connecticut"
    query = "medical malpractice lawyers"
    cities = [
    # "Andover","Ansonia","Ashford","Avon","Barkhamsted","Beacon Falls","Berlin","Bethany",
    "Bethel","Bethlehem",
    "Bloomfield","Bolton","Bozrah","Branford","Bridgeport","Bridgewater","Bristol","Brookfield","Brooklyn","Burlington",
    "Canaan","Canterbury","Canton","Chaplin","Cheshire","Chester","Clinton","Colchester","Colebrook","Columbia",
    "Cornwall","Coventry","Cromwell","Danbury","Darien","Deep River","Derby","Durham","East Granby","East Haddam",
    "East Hampton","East Hartford","East Haven","East Lyme","East Windsor","Eastford","Easton","Ellington","Enfield","Essex",
    "Fairfield","Farmington","Franklin","Glastonbury","Goshen","Granby","Greenwich","Griswold","Groton","Guilford",
    "Haddam","Hamden","Hampton","Hartland","Harwinton","Hebron","Kent","Killingly","Killingworth",
    "Lebanon","Ledyard","Lisbon","Litchfield","Lyme","Madison","Manchester","Mansfield","Marlborough","Meriden",
    "Middlebury","Middlefield","Middletown","Milford","Monroe","Montville","Morris","Naugatuck","New Britain","New Canaan",
    "New Fairfield","New Hartford","New London","New Milford","Newington","Newtown","Norfolk","North Branford","North Canaan",
    "North Haven","North Stonington","Norwalk","Norwich","Old Lyme","Old Saybrook","Orange","Oxford","Plainfield","Plainville",
    "Plymouth","Pomfret","Portland","Preston","Prospect","Putnam","Redding","Ridgefield","Rocky Hill","Roxbury",
    "Salem","Salisbury","Scotland","Seymour","Sharon","Shelton","Sherman","Simsbury","Somers","South Windsor",
    "Southbury","Southington","Sprague","Stafford","Stamford","Sterling","Stonington","Stratford","Suffield","Thomaston",
    "Thompson","Tolland","Torrington","Trumbull","Union","Vernon","Voluntown","Wallingford","Warren","Washington",
    "Waterbury","Waterford","Watertown","West Hartford","West Haven","Westbrook","Weston","Westport","Wethersfield","Willington",
    "Wilton","Winchester","Windham","Windsor","Windsor Locks","Wolcott","Woodbridge","Woodbury","Woodstock"
]
    

    for city in cities:
        # query = input("Enter query (example: medical malpractice lawyers): ")
        # city = input("Enter city: ")
        # state = input("Enter state: ")
        query = f"{query} in {city}(USA)"
        target = 200
        # target = int(input("How many firms?: "))

        profile_names = get_lead_profile_names()

        log(f"Starting lead generation for {city}, {state} with query: '{query}' and target: {target}", log_path)
        log(f"Existing profile names loaded: {len(profile_names)}", log_path)

        firms = FirmParser(log_path=log_path).scrape_google_places(query, target)
        log(firms, log_path)
        df = pd.DataFrame(firms)

        df.to_csv(f"google_places_firms_{city}_{state}.csv", index=False)

        # if os.path.exists(profile_file_name):
        #     os.remove(profile_file_name)
        #     log("Previous Attorney File deleted successfully.", log_path)

        # Define your column names
        # headers = ['Name', 'Phone', 'Email', 'Profile URL']

        # with open(profile_file_name, mode='w', newline='', encoding='utf-8') as file:
        #     writer = csv.writer(file)
        #     writer.writerow(headers)

        # Start worker threads
        log(firms, log_path)   
        for firm in firms:
            firm_queue.put(firm)

        # Firm workers
        for _ in range(4):
            Thread(target=firm_worker, daemon=True).start()

        # Email verification workers
        for _ in range(10):
            Thread(target=email_worker, args=(city, state, profile_names), daemon=True).start()

        # CSV writer
        Thread(target=csv_writer, daemon=True).start() 

        Thread(target=monitor, daemon=True).start()

        firm_queue.join()
        profile_queue.join()
        result_queue.join()