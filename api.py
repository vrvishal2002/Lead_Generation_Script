import threading
import uuid
import time
import os
import csv
from pathlib import Path
import glob
from contextlib import asynccontextmanager
import pandas as pd
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Dict, Any
from lead_generation_helper import LeadGenerationHelper
from firm_parser import FirmParser
from log_lib import log, get_domain_names, get_lead_profile_names

shutdown_event = threading.Event()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    yield
    # Shutdown logic
    log("Shutdown signal received. Cleaning up jobs...")
    shutdown_event.set()

app = FastAPI(title="Lead Generation API", lifespan=lifespan)

@app.get("/")
async def serve_dashboard():
    """Serves the interactive Lead Generation Dashboard from a file."""
    return FileResponse("dashboard.html")

@app.get("/health")
async def health_check():
    """API Health Check."""
    return {"status": "online", "active_jobs": len(active_jobs)}

@app.get("/results")
async def list_results():
    """Lists all CSV files found in the attorney_profiles directory."""
    files = glob.glob("attorney_profiles/**/*.csv", recursive=True)
    # Sort by modification time to show newest first
    files.sort(key=os.path.getmtime, reverse=True)
    return {"files": [f.replace("\\", "/") for f in files]}

@app.get("/results/view")
async def view_result(path: str = Query(...)):
    """Reads a CSV file and returns the data as JSON."""
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        df = pd.read_csv(path)
        return df.fillna("").to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/results/download")
async def download_result(path: str = Query(...)):
    """Serves the CSV file for download."""
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, filename=os.path.basename(path))

@app.post("/results/consolidate")
async def consolidate_results():
    """Merges all CSV files into a master file."""
    file_path = "attorney_profiles/attorney_profiles_final_New Haven_Connecticut.csv"

    data = []

    folder_path = "attorney_profiles"

    all_data = []

    files_count = len(list(Path(folder_path).rglob("*.csv")))
    for file_path in Path(folder_path).rglob("*.csv"):
            
        print(f"Reading: {file_path}")
        
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f)
            
            for row in reader:
                data.append(row)

    # Print result
    email_set = set()
    consolidated_data = []
    for row in data:
        if row[2] not in email_set:  # Assuming email is in the third column (index 2)
            consolidated_data.append(row)
        else:
            print(f"Duplicate email found and skipped: {row[2]}")  # Optional: log duplicate emails
        email_set.add(row[2])  # Assuming email is in the third column (index 2)

    header = ["Name", "Phone", "Email", "Profile URL", "Company", "City", "State"]

    if not os.path.exists(f"attorney_profiles/consolidated"):
        os.makedirs(f"attorney_profiles/consolidated")
    with open("attorney_profiles/consolidated/attorney_profiles_master.csv", mode="w", encoding="utf-8", newline='') as file:
        writer = csv.writer(file)
        
        # Write header first
        writer.writerow(header)
        
        # Write existing rows
        for row in consolidated_data:
            writer.writerow(row)

        return {"message": f"Consolidated {files_count} files into {file}. Total unique leads: {len(consolidated_data)}."}
    
    return {"message": "Consolidation failed or no valid files found."}

# In-memory storage for active jobs. In production, use Redis or a DB.
active_jobs: Dict[str, LeadGenerationHelper] = {}

class ScrapeRequest(BaseModel):
    state: str
    cities: List[str] = []
    base_query: str = "medical malpractice and personal injury lawyers"
    target: int = 10

@app.on_event("startup")
async def startup_event():
    # Any startup tasks can go here
    pass

@app.on_event("shutdown")
async def shutdown_event_handler():
    log("Shutdown signal received. Cleaning up jobs...")
    shutdown_event.set()


def run_job_logic(job_id: str, request: ScrapeRequest):
    """Replicates the main loop and run_city logic from lead_generation_script_2.py"""
    helper = active_jobs[job_id]
    domains_with_no_leads = set()
    log_path = helper.log_path

    def wait_for_queue(q):
        while not q.empty() or q.unfinished_tasks > 0:
            if shutdown_event.is_set(): return
            time.sleep(1)

    # Start worker threads (matching scripts counts)
    threads = []
    for _ in range(4):
        # 4 Firm workers as per script
        threads.append(threading.Thread(target=helper.firm_worker, daemon=True))

    threads.append(threading.Thread(target=helper.csv_writer, daemon=True))
    threads.append(threading.Thread(target=helper.monitor, daemon=True))
    
    # 10 Email workers as per script
    for _ in range(10):
        threads.append(threading.Thread(target=helper.email_worker, daemon=True))

    for t in threads:
        t.start()

    try:
      for city in request.cities:
        if shutdown_event.is_set():
            break
            
        helper.city = city
        helper.state = request.state
        helper.domains_with_no_leads = domains_with_no_leads
        helper.gathered_domain_names = get_domain_names()
        helper.gathered_profile_names = get_lead_profile_names()

        query = f"{request.base_query} in {city}, {request.state}"

        log(f"--- Starting API Job: {city}, {request.state} ---", log_path)
        firms = FirmParser(log_path=log_path).scrape_google_places(query, request.target)

        if not firms:
            log(f"No firms found for {city}, {request.state}.", log_path)
            continue

        # Save firms detail as script does
        os.makedirs("Firms_details", exist_ok=True)
        pd.DataFrame(firms).to_csv(f"Firms_details/google_places_firms_{city}_{request.state}.csv", index=False)

        for firm in firms:
            helper.firm_queue.put(firm)

        # Wait for queues to empty with shutdown awareness
        wait_for_queue(helper.firm_queue)
        if shutdown_event.is_set(): break
        
        wait_for_queue(helper.profile_queue)
        if shutdown_event.is_set(): break
        
        wait_for_queue(helper.result_queue)
        if shutdown_event.is_set(): break

        # Update persistent empty domains set from the helper's session cache
        with helper.domain_with_no_profiles_cache_lock:
            for domain, has_no_profiles in helper.domain_with_no_profiles_cache.items():
                if has_no_profiles:
                    domains_with_no_leads.add(domain)

    finally:
        # Signal workers to exit
        # Send one sentinel per worker thread started
        for _ in range(4):
            helper.firm_queue.put(None)
            
        for _ in range(10):
            helper.profile_queue.put(None)
            
        helper.result_queue.put(None)
        helper.monitor_queue.put(None) # Signal monitor to stop
        
        # Quick final join
        wait_for_queue(helper.firm_queue)
        wait_for_queue(helper.profile_queue)
        wait_for_queue(helper.result_queue)

    # Cleanup the job from memory
    active_jobs.pop(job_id, None)
    log(f"Job {job_id} completed all cities.", log_path)

@app.post("/jobs/start")
async def start_job(request: ScrapeRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    helper = LeadGenerationHelper(state=request.state)
    
    active_jobs[job_id] = helper
    background_tasks.add_task(run_job_logic, job_id, request)
    
    return {"job_id": job_id, "message": "Scraping job started successfully"}

@app.get("/jobs/{job_id}/status")
async def get_job_status(job_id: str):
    if job_id not in active_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    helper = active_jobs[job_id]
    return {
        "job_id": job_id,
        "queues": {
            "firms_remaining": helper.firm_queue.qsize(),
            "profiles_pending": helper.profile_queue.qsize(),
            "results_processed": helper.result_queue.qsize()
        }
    }

@app.get("/jobs/active")
async def get_active_jobs():
    """Returns status for all currently running jobs."""
    return {
        job_id: {
            "city": helper.city,
            "state": helper.state,
            "queues": {
                "firms": helper.firm_queue.qsize(),
                "profiles": helper.profile_queue.qsize(),
                "results": helper.result_queue.qsize()
            }
        } for job_id, helper in active_jobs.items()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)