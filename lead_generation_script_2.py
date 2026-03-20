from threading import Thread
import threading
import pandas as pd
import lead_generation_helper
from log_lib import get_log_name, log, get_lead_profile_names, get_domain_names
from lead_generation_helper import csv_writer, firm_queue, profile_queue, result_queue, firm_worker, email_worker, monitor
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


def run_city(city, state, query, target):
    profile_names = get_lead_profile_names()
    domain_names = get_domain_names()

    domain_no_profiles_cache = {}
    domain_no_profiles_cache_lock = threading.Lock()

    log(f"Starting lead generation for {city}, {state} with query: '{query}' and target: {target}", log_path)
    log(f"Existing profile names loaded: {len(profile_names)}", log_path)
    log(f"Existing domain names loaded: {len(domain_names)}", log_path)

    firms = FirmParser(log_path=log_path).scrape_google_places(query, target)
    log(firms, log_path)

    if not firms:
        log(f"No firms found for {city}, {state}.", log_path)
        return

    pd.DataFrame(firms).to_csv(f"Firms_details/google_places_firms_{city}_{state}.csv", index=False)

    for firm in firms:
        firm_queue.put(firm)

    for _ in range(FIRM_WORKER_COUNT):
        Thread(target=firm_worker, args=(domain_names, domains_with_no_leads), daemon=True).start()

    for _ in range(EMAIL_WORKER_COUNT):
        Thread(
            target=email_worker,
            args=(city, state, profile_names, domain_no_profiles_cache, domain_no_profiles_cache_lock),
            daemon=True,
        ).start()

    Thread(target=csv_writer, daemon=True).start()
    Thread(target=monitor, daemon=True).start()

    firm_queue.join()
    profile_queue.join()
    result_queue.join()

    for domain, has_no_profiles in domain_no_profiles_cache.items():
        if has_no_profiles:
            log(f"Domain {domain} was found to have no profiles with valid emails. Consider skipping this domain in future runs.", log_path)
            domains_with_no_leads.add(domain)

    close_driver()


if __name__ == "__main__":
    state = "Connecticut"
    cities = [
    # "Andover","Ansonia","Ashford","Avon","Barkhamsted","Beacon Falls","Berlin","Bethany","Bethel","Bethlehem","Bloomfield",
    # "Bolton","Bozrah","Branford","Bridgeport","Bridgewater","Bristol","Brookfield","Brooklyn","Burlington",
    # "Canaan","Canterbury","Canton","Chaplin","Cheshire","Chester","Clinton","Colchester","Colebrook","Columbia",
    "Cornwall","Coventry","Cromwell","Danbury","Darien","Deep River","Derby","Durham","East Granby","East Haddam",
    "East Hampton","East Hartford","East Haven","East Lyme","East Windsor","Eastford","Easton","Ellington","Enfield","Essex",
    # "Fairfield","Farmington","Franklin","Glastonbury","Goshen","Granby","Greenwich","Griswold","Groton","Guilford",
    # "Haddam","Hamden","Hampton","Hartland","Harwinton","Hebron","Kent","Killingly","Killingworth",
    # "Lebanon","Ledyard","Lisbon","Litchfield","Lyme","Madison","Manchester","Mansfield","Marlborough","Meriden",
    # "Middlebury","Middlefield","Middletown","Milford","Monroe","Montville","Morris","Naugatuck","New Britain","New Canaan",
    # "New Fairfield","New Hartford","New London","New Milford","Newington","Newtown","Norfolk","North Branford","North Canaan",
    # "North Haven","North Stonington","Norwalk","Norwich","Old Lyme","Old Saybrook","Orange","Oxford","Plainfield","Plainville",
    # "Plymouth","Pomfret","Portland","Preston","Prospect","Putnam","Redding","Ridgefield","Rocky Hill","Roxbury",
    # "Salem","Salisbury","Scotland","Seymour","Sharon","Shelton","Sherman","Simsbury","Somers","South Windsor",
    # "Southbury","Southington","Sprague","Stafford","Stamford","Sterling","Stonington","Stratford","Suffield","Thomaston",
    # "Thompson","Tolland","Torrington","Trumbull","Union","Vernon","Voluntown","Wallingford","Warren","Washington",
    # "Waterbury","Waterford","Watertown","West Hartford","West Haven","Westbrook","Weston","Westport","Wethersfield","Willington",
    # "Wilton","Winchester","Windham","Windsor","Windsor Locks","Wolcott","Woodbridge","Woodbury","Woodstock"
]
    

    base_query = "medical malpractice lawyers"
    target = 200

    for city in cities:
        query = f"{base_query} in {city} USA CT"
        run_city(city=city, state=state, query=query, target=target)
