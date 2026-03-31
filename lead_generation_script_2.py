from concurrent.futures import ThreadPoolExecutor
from threading import Thread
import threading
import pandas as pd
import lead_generation_helper
import time
from log_lib import get_log_name, log, get_lead_profile_names, get_domain_names
from lead_generation_helper import firm_queue, profile_queue, result_queue
from firm_parser import FirmParser
from soup_content_lib import close_driver

# --------------- For Windows to handle potential freezes ----------------
# faulthandler.enable(file=sys.stderr)
# faulthandler.dump_traceback_later(60, repeat=True)
#-------------------------------------------------------------------------


FIRM_WORKER_COUNT = 4
EMAIL_WORKER_COUNT = 10

log_path = get_log_name()
lead_generation_helper.log_path = log_path
domains_with_no_leads = set()


def run_city(city, state, query, target, leadGenretionHelper):
    leadGenretionHelper.city = city
    leadGenretionHelper.state = state
    leadGenretionHelper.domains_with_no_leads = domains_with_no_leads

    leadGenretionHelper.gathered_domain_names = get_domain_names()
    leadGenretionHelper.gathered_profile_names = get_lead_profile_names()

    domain_no_profiles_cache = {}

    log(f"Starting lead generation for {city}, {state} with query: '{query}' and target: {target}", log_path)
    log(f"Existing profile names loaded: {len(leadGenretionHelper.gathered_profile_names)}", log_path)
    log(f"Existing domain names loaded: {len(leadGenretionHelper.gathered_domain_names)}", log_path)

    log(f"--- Starting: {city}, {state} ---", log_path)
    firms = FirmParser(log_path=log_path).scrape_google_places(query, target)
    log(firms, log_path)

    if not firms:
        log(f"No firms found for {city}, {state}.", log_path)
        return

    pd.DataFrame(firms).to_csv(f"Firms_details/google_places_firms_{city}_{state}.csv", index=False)

    for firm in firms:
        firm_queue.put(firm)


    # Wait for queues to empty with logging
    while not firm_queue.empty():
        time.sleep(1)
    firm_queue.join()
    log("Firm queue joined.", log_path)

    while not profile_queue.empty():
        time.sleep(1)
    profile_queue.join()
    log("Profile queue joined.", log_path)

    while not result_queue.empty():
        time.sleep(1)
    result_queue.join()
    log("Result queue joined.", log_path)

    for domain, has_no_profiles in domain_no_profiles_cache.items():
        if has_no_profiles:
            log(f"Domain {domain} was found to have no profiles with valid emails. Consider skipping this domain in future runs.", log_path)
            domains_with_no_leads.add(domain)

    # close_driver()


if __name__ == "__main__":
    state = "Missouri"
    cities = [
    # "Kansas City", "St. Louis", 
    # "Springfield", "Columbia", "Independence", "O'Fallon", "St. Charles",
    "Lee's Summit", "St. Joseph", "St. Peters", 
    "Blue Springs", "Joplin", "Florissant", "Wentzville", "Chesterfield", 
    "Jefferson City", "Cape Girardeau", "Wildwood", "University City", "Liberty", 
    "Ballwin", "Kirkwood", "Raytown", "Gladstone", "Nixa", 
    "Raymore", "Maryland Heights", "Belton", "Grandview", "Hazelwood", 
    "Webster Groves", "Ozark", "Sedalia", "Republic", "Arnold", 
    "Rolla", "Warrensburg", "Farmington", "Lake St. Louis", "Creve Coeur", 
    "Manchester", "Ferguson", "Kirksville", "Clayton", "Hannibal", 
    "Poplar Bluff", "Grain Valley", "Sikeston", "Jackson", "Carthage", 
    "Overland", "Lebanon", "Washington", "Troy", "Moberly", 
    "Marshall", "Dardenne Prairie", "Festus", "Webb City", "Eureka", 
    "Union", "Jennings", "Branson", "St. Ann", "West Plains", 
    "Fulton", "Crestwood", "Town and Country", "Bolivar", "Mexico", 
    "Excelsior Springs", "Bridgeton", "Bellefontaine Neighbors", "Maryville", "Kennett", 
    "Cottleville", "Harrisonville", "Clinton", "Chillicothe", "Kearney", 
    "Smithville", "Carl Junction", "Ellisville", "Ladue", "Richmond Heights", 
    "Monett", "Neosho", "Clinton", "Parkville", "Des Peres", 
    "Sullivan", "Berkeley", "Brentwood", "Aurora", "Boonville", 
    "Nevada", "Warrenton", "Sunset Hills", "Richmond", "Shrewsbury"
]
    leadGenretionHelper = lead_generation_helper.LeadGenerationHelper(state=state)
    # Initialize the helper class to set up log path and any other necessary attributes

    # Start Workers as Daemon Threads
    for _ in range(FIRM_WORKER_COUNT):
        t = Thread(target=leadGenretionHelper.firm_worker, daemon=True)
        t.start()
    
    for _ in range(EMAIL_WORKER_COUNT):
        t = Thread(target=leadGenretionHelper.email_worker, daemon=True)
        t.start()

    t_writer = Thread(target=leadGenretionHelper.csv_writer, daemon=True)
    t_writer.start()

    t_monitor = Thread(target=leadGenretionHelper.monitor, daemon=True)
    t_monitor.start()

    # Main Loop
    base_query = "medical malpractice and personal injury lawyers"
    target = 1000

    for city in cities:
        leadGenretionHelper.city = city
        leadGenretionHelper.state = state
        query = f"{base_query} in {city}, {state}"
        run_city(city, state, query, target, leadGenretionHelper)


    