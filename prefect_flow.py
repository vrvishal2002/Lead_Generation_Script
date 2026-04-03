import os
import threading
import time
import io
import pandas as pd
import boto3
from prefect import flow, task
from lead_generation_helper import LeadGenerationHelper
from firm_parser import FirmParser
from log_lib import log, get_domain_names, get_lead_profile_names

S3_BUCKET = os.environ.get("S3_BUCKET_NAME")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

@task(name="Scrape Firms")
def get_firms(query, target, log_path):
    return FirmParser(log_path=log_path).scrape_google_places(query, target)

@flow(name="Lead Generation Scraper")
def scraping_flow(state: str = "Missouri", cities: list = ["Lee's Summit"], base_query: str = "medical malpractice and personal injury lawyers", target: int = 10):
    helper = LeadGenerationHelper(state=state)
    domains_with_no_leads = set()
    log_path = helper.log_path

    # Start Background Workers
    threads = []
    for _ in range(4):
        threads.append(threading.Thread(target=helper.firm_worker, daemon=True))
    for _ in range(10):
        threads.append(threading.Thread(target=helper.email_worker, daemon=True))
    threads.append(threading.Thread(target=helper.csv_writer, daemon=True))
    
    for t in threads:
        t.start()

    for city in cities:
        helper.city = city
        helper.state = state
        helper.domains_with_no_leads = domains_with_no_leads
        helper.gathered_domain_names = get_domain_names()
        helper.gathered_profile_names = get_lead_profile_names()

        query = f"{base_query} in {city}, {state}"
        
        # Execute scraping task
        firms = get_firms(query, target, log_path)

        if not firms:
            continue

        file_key = f"Firms_details/google_places_firms_{city}_{state}.csv"
        if S3_BUCKET:
            s3_client = boto3.client('s3', region_name=AWS_REGION)
            csv_buffer = io.StringIO()
            pd.DataFrame(firms).to_csv(csv_buffer, index=False)
            s3_client.put_object(Bucket=S3_BUCKET, Key=file_key, Body=csv_buffer.getvalue().encode('utf-8'))
        else:
            os.makedirs("Firms_details", exist_ok=True)
            pd.DataFrame(firms).to_csv(file_key, index=False)

        for firm in firms:
            helper.firm_queue.put(firm)

        # Wait for this city to finish
        while not helper.firm_queue.empty() or helper.firm_queue.unfinished_tasks > 0:
            time.sleep(2)
        while not helper.profile_queue.empty() or helper.profile_queue.unfinished_tasks > 0:
            time.sleep(2)
        while not helper.result_queue.empty() or helper.result_queue.unfinished_tasks > 0:
            time.sleep(2)

    # Shutdown Workers
    for _ in range(4): helper.firm_queue.put(None)
    for _ in range(10): helper.profile_queue.put(None)
    helper.result_queue.put(None)
    
    return {"status": "completed", "state": state, "cities_processed": cities}

if __name__ == "__main__":
    scraping_flow(
        state="Missouri", 
        cities=["Lee's Summit", "St. Joseph"]
    )