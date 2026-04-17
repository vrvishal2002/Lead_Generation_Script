import threading
import uuid
import time
import os
import csv, io
import boto3
from pathlib import Path
import glob
from contextlib import asynccontextmanager
import pandas as pd
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, File, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Dict
from lead_generation_helper import LeadGenerationHelper
from firm_parser import FirmParser
from log_lib import log, get_domain_names, get_lead_profile_names, csv_file_lock

S3_BUCKET = os.environ.get("S3_BUCKET_NAME")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
s3_client = boto3.client('s3', region_name=AWS_REGION) if S3_BUCKET else None

shutdown_event = threading.Event()

def is_safe_path(path: str) -> bool:
    """Validates that the path is within allowed directories."""
    base_dirs = [
        os.path.abspath("attorney_profiles"),
        os.path.abspath("Firms_details"),
        os.path.abspath("attorney_profiles/saved")
    ]
    try:
        target_path = os.path.abspath(path)
        return any(target_path.startswith(d) for d in base_dirs)
    except Exception:
        return False

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    if S3_BUCKET:
        try:
            # Attempt to list objects to verify IAM permissions and bucket existence
            s3_client.list_objects_v2(Bucket=S3_BUCKET, MaxKeys=1)
            print(f"AWS CONFIG SUCCESS: Successfully connected to S3 bucket: {S3_BUCKET}")
        except Exception as e:
            print(f"AWS CONFIG ERROR: Critical failure connecting to S3. Check IAM Role and S3_BUCKET_NAME.")
            print(f"Error Detail: {str(e)}")

    yield
    # Shutdown logic
    log("Shutdown signal received. Cleaning up jobs...")
    shutdown_event.set()
    # Also cancel all active jobs on API shutdown
    for job_id in list(active_jobs.keys()):
        helper = active_jobs.get(job_id)
        if helper:
            helper.cancel_event.set()
            # Unblock any waiting workers
            for _ in range(20): helper.firm_queue.put(None)
            for _ in range(20): helper.profile_queue.put(None)
            helper.result_queue.put(None)

app = FastAPI(title="Lead Generation API", lifespan=lifespan)

@app.get("/")
async def serve_dashboard():
    """Serves the interactive Lead Generation Dashboard from a file."""
    return FileResponse("dashboard.html")

@app.get("/health")
async def health_check():
    """API Health Check."""
    return {"status": "healthy", "active_jobs": len(active_jobs)}

@app.delete("/results/delete")
async def delete_file(path: str = Query(...), password: str = Query(...)):
    """Deletes a file if the password is correct."""
    if password != "admin123":
        raise HTTPException(status_code=403, detail="Invalid password.")
    
    if S3_BUCKET:
        try:
            s3_client.delete_object(Bucket=S3_BUCKET, Key=path)
            return {"message": "File deleted from S3"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        if not is_safe_path(path) or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="File not found")
        try:
            os.remove(path)
            return {"message": "File deleted successfully"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/results")
async def list_results():
    """Lists all CSV files found in the attorney_profiles directory."""
    results = {}
    if S3_BUCKET:
        try:
            resp = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix="attorney_profiles/")
            # Sort S3 objects chronologically ascending
            contents = sorted(resp.get('Contents', []), key=lambda x: x['LastModified'])
            files = [obj['Key'] for obj in contents if obj['Key'].endswith('.csv')]
            files = [f for f in files if all(x not in f for x in ["uploaded/", "consolidated/", "saved/"])]
            
            # Group by state (folder)
            for f in files:
                parts = f.split('/')
                state = parts[1] if len(parts) > 2 else "Uncategorized"
                if state not in results: results[state] = []
                results[state].append(f)
            return results
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"S3 Error: {str(e)}")
        
    files = glob.glob("attorney_profiles/**/*.csv", recursive=True)
    files = [f for f in files if all(x not in f.replace("\\", "/").split("/") for x in ["uploaded", "consolidated", "saved"])]
    
    # Sort by modification time ascending (oldest first)
    files.sort(key=os.path.getmtime, reverse=False)

    for f in files:
        f_clean = f.replace("\\", "/")
        parts = f_clean.split("/")
        # Expected: attorney_profiles/StateName/filename.csv
        if len(parts) > 2:
            state = parts[1]
        else:
            state = "Uncategorized"
            
        if state not in results:
            results[state] = []
        results[state].append(f_clean)

    return results


def get_file(path: str):
    if S3_BUCKET:
        try:
            obj = s3_client.get_object(Bucket=S3_BUCKET, Key=path)
            f = io.StringIO(obj['Body'].read().decode('utf-8'))
        except s3_client.exceptions.NoSuchKey:
            raise HTTPException(status_code=404, detail="S3 file not found")
    else:
        if not is_safe_path(path) or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="File not found")
        f = open(path, encoding="utf-8", errors="ignore")
        
    return f


@app.get("/results/view")
async def view_result(path: str = Query(...)):
    """Reads a CSV file and returns the data as JSON."""
    try:
        
        f = get_file(path)
        headers = ["S.No", "Name", "Phone", "Email", "Profile URL", "Company", "City", "State", "Verified By"]
        data = []
        
        with csv_file_lock:
            reader = csv.reader(f)
            count = 0
            for row in reader:
                # Skip empty rows or rows that are just commas/whitespace
                if not row or all(not cell.strip() for cell in row): continue
                entry = {}
                entry["S.No"] = 1 + count
                row_index = 0
                for index in range(1, len(headers)):
                    # Logic to handle missing "Verified By" column gracefully in memory
                    if row_index < len(row) and row[row_index] in headers:
                        entry = {}
                        break
                    if index == len(headers) - 1 and (row_index >= len(row) or row_index == len(row) - 1 and row[row_index] == ""):
                        entry[headers[index]] = "verified by smtp"
                    elif row_index < len(row):
                        # Skip boolean artifacts if present
                        if row[row_index] in (True, False, "True", "False") and row_index + 1 < len(row):
                            row_index += 1
                        entry[headers[index]] = row[row_index]
                        row_index += 1
                    else:
                        entry[headers[index]] = ""  
                if entry:
                    count += 1
                    data.append(entry)


            if S3_BUCKET and data:
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(headers[1:]) # MUST write headers back
                for row in data:
                    # Convert dict values to list, skipping S.No which is for UI only
                    writer.writerow(list(row.values())[1:])
                s3_client.put_object(Bucket=S3_BUCKET, Key=path, Body=output.getvalue().encode('utf-8'))
            elif not S3_BUCKET and data:
                with open(path, mode="w", encoding="utf-8", newline='') as f_out:
                    writer = csv.writer(f_out)
                    writer.writerow(headers[1:]) # MUST write headers back
                    # Write existing rows (skipping S.No added for UI)
                    for row in data:
                        writer.writerow(list(row.values())[1:])
        

        return data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/results/download")
async def download_result(path: str = Query(...)):
    """Serves the CSV file for download."""
    if S3_BUCKET:
        try:
            obj = s3_client.get_object(Bucket=S3_BUCKET, Key=path)
            return StreamingResponse(
                io.BytesIO(obj['Body'].read()),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={os.path.basename(path)}"}
            )
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"S3 File not found: {str(e)}")
    else:
        if not is_safe_path(path) or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(path, filename=os.path.basename(path))

@app.get("/results/uploaded")
async def list_uploaded():
    """Lists all CSV files found in the attorney_profiles/uploaded directory."""
    if S3_BUCKET:
        try:
            resp = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix="attorney_profiles/uploaded/")
            files = [obj['Key'] for obj in resp.get('Contents', []) if obj['Key'].endswith('.csv')]
            return {"files": files}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"S3 Error: {str(e)}")
    else:
        files = glob.glob("attorney_profiles/uploaded/*.csv")
        files.sort(key=os.path.getmtime, reverse=True)
    return {"files": [f.replace("\\", "/") for f in files]}

@app.post("/results/upload")
async def upload_leads(file: UploadFile = File(...)):
    """Uploads a leads CSV file, validates its structure, and saves it."""
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are allowed.")

    file_path = None
    s3_path = None
    try:
        content = await file.read()
        if S3_BUCKET:
            s3_path = f"attorney_profiles/uploaded/{file.filename}"
            s3_client.put_object(Bucket=S3_BUCKET, Key=s3_path, Body=content)
        else:
            upload_dir = Path("attorney_profiles/uploaded")
            upload_dir.mkdir(parents=True, exist_ok=True)
            file_path = upload_dir / file.filename
            with file_path.open("wb") as buffer:
                buffer.write(content)

        # Validate CSV content
        if not content or len(content.strip()) == 0:
            raise HTTPException(status_code=400, detail="The uploaded CSV file is empty.")
        
        df = pd.read_csv(io.BytesIO(content))

        all_cols = ["Name", "Phone", "Email", "Profile URL", "Company", "City", "State", "Verified By"]
        compulsory_cols = ["Name", "Email", "Profile URL", "Company", "City", "State", "Verified By"]
        
        # 1. Validate Header Structure
        missing_headers = [col for col in all_cols if col not in df.columns]
        if missing_headers:
            if S3_BUCKET:
                s3_client.delete_object(Bucket=S3_BUCKET, Key=s3_path)
            else:
                os.remove(file_path)
            raise HTTPException(status_code=400, detail=f"Invalid structure. Missing columns: {', '.join(missing_headers)}")
            
        # 2. Validate Compulsory Data (Everything except Phone)
        # Check for nulls or empty strings in compulsory columns
        is_empty = df[compulsory_cols].isna() | (df[compulsory_cols].astype(str).apply(lambda x: x.str.strip()) == "")
        if is_empty.any().any():
            if S3_BUCKET:
                s3_client.delete_object(Bucket=S3_BUCKET, Key=s3_path)
            else:
                os.remove(file_path)
            raise HTTPException(status_code=400, detail="Validation failed. Mandatory fields (Name, Email, URL, Company, City, State, Verified By) cannot be empty.")
            
        return {"message": f"File '{file.filename}' uploaded successfully.", "rows": len(df)}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        if not S3_BUCKET and file_path and file_path.exists():
            os.remove(file_path)
        elif S3_BUCKET and s3_path:
            try: s3_client.delete_object(Bucket=S3_BUCKET, Key=s3_path)
            except: pass
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/results/consolidated")
async def list_consolidated():
    """Lists all CSV files found in the attorney_profiles/consolidated directory."""
    if S3_BUCKET:
        resp = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix="attorney_profiles/consolidated/")
        files = [obj['Key'] for obj in resp.get('Contents', []) if obj['Key'].endswith('.csv')]
        return {"files": files}

    files = glob.glob("attorney_profiles/consolidated/*.csv")
    files.sort(key=os.path.getmtime, reverse=True)
    return {"files": [f.replace("\\", "/") for f in files]}

@app.get("/results/saved")
async def list_saved():
    """Lists all CSV files in the saved directory."""
    if S3_BUCKET:
        try:
            resp = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix="attorney_profiles/saved/")
            contents = sorted(resp.get('Contents', []), key=lambda x: x['LastModified'])
            files = [obj['Key'] for obj in contents if obj['Key'].endswith('.csv')]
            return {"files": files}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
            
    files = glob.glob("attorney_profiles/saved/*.csv")
    files.sort(key=os.path.getmtime, reverse=False)
    return {"files": [f.replace("\\", "/") for f in files]}

@app.post("/jobs/{job_id}/save")
async def save_job_results(job_id: str):
    """Saves all currently generated leads for a job into a single file."""
    if job_id not in active_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    helper = active_jobs[job_id]
    leads = helper.status.get("recent_leads", [])
    if not leads:
        return {"message": "No leads found to save yet."}
    
    df = pd.DataFrame(leads)
    filename = f"job_result_{helper.state}_{int(time.time())}.csv"
    
    if S3_BUCKET:
        s3_path = f"attorney_profiles/saved/{filename}"
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        s3_client.put_object(Bucket=S3_BUCKET, Key=s3_path, Body=csv_buffer.getvalue().encode('utf-8'))
    else:
        save_dir = Path("attorney_profiles/saved")
        save_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(save_dir / filename, index=False)
        
    return {"message": f"Saved {len(leads)} leads to {filename}"}

class ConsolidateRequest(BaseModel):
    filename: str = "attorney_profiles_master"
    include_uploaded: bool = False

@app.post("/results/consolidate")
async def consolidate_results(request: ConsolidateRequest):
    """Merges CSV files into a master file with custom naming and optional upload inclusion."""
    files_to_process = []
    if S3_BUCKET:
        resp = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix="attorney_profiles/")
        contents = resp.get('Contents', [])
        
        for obj in contents:
            key = obj['Key']
            if not key.endswith('.csv'): continue
            parts = key.split('/')
            if all(x not in parts for x in ["uploaded", "consolidated", "saved"]):
                files_to_process.append(key)
            elif request.include_uploaded and "uploaded" in parts:
                files_to_process.append(key)
    else:
        folder_path = "attorney_profiles"
        # Identify Generated Files
        generated_files = glob.glob(f"{folder_path}/**/*.csv", recursive=True)
        generated_files = [f for f in generated_files if "uploaded" not in f.replace("\\", "/").split("/") and "consolidated" not in f.replace("\\", "/").split("/")]
        
        files_to_process = generated_files
        
        # Optionally include Uploaded Files
        if request.include_uploaded:
            uploaded_files = glob.glob(f"{folder_path}/uploaded/*.csv")
            files_to_process.extend(uploaded_files)

    if not files_to_process:
        return {"message": "No files found to consolidate."}

    data = []
    for f_path in files_to_process:
        with csv_file_lock:
            with get_file(f_path) as f:
                reader = csv.reader(f)
                file_rows = list(reader)
                if not file_rows: continue
                # Skip header if present
                start_idx = 1 if file_rows[0] and file_rows[0][0] == "Name" else 0
                data.extend(file_rows[start_idx:])

    email_set = set()
    consolidated_data = []
    for row in data:
        if not row or len(row) < 3: continue
        email = row[2].strip().lower()
        if email not in email_set:
            consolidated_data.append(row)
            if email: email_set.add(email)

    header = ["Name", "Phone", "Email", "Profile URL", "Company", "City", "State", "Verified By"]

    clean_name = request.filename.replace(".csv", "") + ".csv"
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    for row in consolidated_data:
        row_to_write = list(row) + [""] * (len(header) - len(row))
        writer.writerow(row_to_write[:len(header)])

    if S3_BUCKET:
        s3_path = f"attorney_profiles/consolidated/{clean_name}"
        s3_client.put_object(Bucket=S3_BUCKET, Key=s3_path, Body=output.getvalue().encode('utf-8'))
    else:
        out_dir = Path("attorney_profiles/consolidated")
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / clean_name, mode="w", encoding="utf-8", newline='') as file:
            file.write(output.getvalue())

    return {"message": f"Consolidated {len(files_to_process)} files into {clean_name}. Total unique leads: {len(consolidated_data)}."}

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
    helper.status["total_cities"] = len(request.cities)
    helper.status["phase"] = "Initializing workers"
    # Initialize per-city status tracking
    for city in request.cities:
        helper.status["cities"][city] = {
            "phase": "Pending",
            "firms_found": 0,
            "firms_target": request.target,
            "leads_found": 0,
            "progress": 0
        }
    
    domains_with_no_leads = set()
    log_path = helper.log_path

    def wait_for_queue(q):
        while not q.empty() or q.unfinished_tasks > 0:
            if shutdown_event.is_set() or helper.cancel_event.is_set(): return
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
    helper.threads = threads

    try:
      for i, city in enumerate(request.cities):
        if shutdown_event.is_set() or helper.cancel_event.is_set():
            break
            
        helper.city = city
        helper.state = request.state
        helper.status["current_city"] = city
        helper.status["cities"][city]["phase"] = "Gathering Firms"
        helper.status["phase"] = f"Processing {city}"
        
        helper.domains_with_no_leads = domains_with_no_leads
        helper.gathered_domain_names = get_domain_names()
        helper.gathered_profile_names = get_lead_profile_names()

        query = f"{request.base_query} in {city}, {request.state}"
        
        def firm_status_callback(count, target):
            helper.status["firms_discovered"] = count
            helper.status["firms_target"] = target
            helper.status["cities"][city]["firms_found"] = count
            # Discovery is 40% of city progress
            discovery_progress = min(100, (count / target) * 100) * 0.4
            helper.status["cities"][city]["progress"] = round(discovery_progress, 1)

            # Progress: Cities done + (This city discovery weight 40%)
            city_base = (i / len(request.cities)) * 100
            discovery_share = (count / target) * (100 / len(request.cities)) * 0.4
            helper.status["overall_progress"] = round(city_base + discovery_share, 2)

        log(f"--- Starting API Job: {city}, {request.state} ---", log_path)
        firms = FirmParser(log_path=log_path).scrape_google_places(
            query, 
            request.target, 
            status_callback=firm_status_callback,
            cancel_event=helper.cancel_event
        )

        if not firms:
            helper.status["cities"][city]["phase"] = "No Firms Found"
            helper.status["cities"][city]["progress"] = 100
            helper.status["cities_completed"] += 1
            log(f"No firms found for {city}, {request.state}.", log_path)
            continue

        helper.status["cities"][city]["phase"] = "Gathering Firms Complete"
        helper.status["cities"][city]["firms_found"] = len(firms)

        # Save firms detail as script does
        file_key = f"Firms_details/google_places_firms_{city}_{request.state}.csv"
        if S3_BUCKET:
            csv_buffer = io.StringIO()
            pd.DataFrame(firms).to_csv(csv_buffer, index=False)
            s3_client.put_object(Bucket=S3_BUCKET, Key=file_key, Body=csv_buffer.getvalue().encode('utf-8'))
        else:
            os.makedirs("Firms_details", exist_ok=True)
            pd.DataFrame(firms).to_csv(file_key, index=False)

        helper.status["cities"][city]["phase"] = "Extracting Leads"
        helper.status["firms_total"] = len(firms)

        # Track leads found for this city specifically
        start_leads = helper.status["profiles_found"]
        for firm in firms:
            if helper.cancel_event.is_set(): break
            helper.firm_queue.put(firm)

        # Wait for queues to empty with shutdown awareness
        wait_for_queue(helper.firm_queue)
        if shutdown_event.is_set(): break
        
        wait_for_queue(helper.profile_queue)
        if shutdown_event.is_set(): break
        
        wait_for_queue(helper.result_queue)
        if shutdown_event.is_set(): break

        helper.status["cities"][city]["leads_found"] = helper.status["profiles_found"] - start_leads
        helper.status["cities"][city]["phase"] = "Done"
        helper.status["cities"][city]["progress"] = 100

        # Update persistent empty domains set from the helper's session cache
        with helper.domain_with_no_profiles_cache_lock:
            for domain, has_no_profiles in helper.domain_with_no_profiles_cache.items():
                if has_no_profiles:
                    domains_with_no_leads.add(domain)
        
        helper.status["cities_completed"] += 1
        helper.status["overall_progress"] = round((helper.status["cities_completed"] / len(request.cities)) * 100, 2)

    except Exception as e:
        helper.status["phase"] = "Error"
        log(f"Job {job_id} encountered error: {e}")
    finally:
        # Determine transitional phase for the UI
        if helper.cancel_event.is_set():
            helper.status["phase"] = "Terminating... Cleaning Up"
        else:
            helper.status["phase"] = "Finishing... Cleaning Up"

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

        # Join threads to wait for current tasks to finish observing the cancel signal
        if hasattr(helper, 'threads'):
            for t in helper.threads:
                t.join(timeout=1)

        # Set final status
        if helper.cancel_event.is_set():
            helper.status["phase"] = "Cancelled"
        elif helper.status["phase"] != "Error":
            helper.status["phase"] = "Completed"

        # Auto-save results on completion or termination
        leads = helper.status.get("recent_leads", [])
        if leads:
            try:
                df = pd.DataFrame(leads)
                filename = f"auto_save_{helper.state}_{job_id[:8]}_{int(time.time())}.csv"
                if S3_BUCKET:
                    s3_path = f"attorney_profiles/saved/{filename}"
                    csv_buffer = io.StringIO()
                    df.to_csv(csv_buffer, index=False)
                    s3_client.put_object(Bucket=S3_BUCKET, Key=s3_path, Body=csv_buffer.getvalue().encode('utf-8'))
                else:
                    save_dir = Path("attorney_profiles/saved")
                    save_dir.mkdir(parents=True, exist_ok=True)
                    df.to_csv(save_dir / filename, index=False)
                log(f"Auto-saved {len(leads)} leads to {filename}", log_path)
            except Exception as e:
                log(f"Auto-save failed for job {job_id}: {e}", log_path)

    # We keep the job in active_jobs for 1 minute after completion so UI can show final status
    log(f"Job {job_id} finished. Phase: {helper.status['phase']}", log_path)
    time.sleep(5)
    active_jobs.pop(job_id, None)

@app.post("/jobs/start")
async def start_job(request: ScrapeRequest, background_tasks: BackgroundTasks):
    # Check if a job for this state is already running to prevent overlap
    if any(j.state == request.state for j in active_jobs.values()):
        raise HTTPException(status_code=400, detail="A job for this state is already running.")
        
    job_id = str(uuid.uuid4())
    helper = LeadGenerationHelper(state=request.state)
    
    active_jobs[job_id] = helper
    background_tasks.add_task(run_job_logic, job_id, request)
    
    return {"job_id": job_id, "message": "Scraping job started successfully"}

@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancels a running job."""
    if job_id not in active_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    helper = active_jobs[job_id]
    helper.cancel_event.set()
    helper.status["phase"] = "Cancelling"
    
    # Sentinel signals to break worker blocks
    for _ in range(10): helper.firm_queue.put(None)
    for _ in range(20): helper.profile_queue.put(None)
    helper.result_queue.put(None)
    
    return {"message": "Cancellation request received."}

@app.get("/jobs/{job_id}/status")
async def get_job_status(job_id: str):
    if job_id not in active_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    helper = active_jobs[job_id]
    status = helper.status.copy()
    status["job_id"] = job_id
    status["queue_depth"] = helper.firm_queue.qsize() + helper.profile_queue.qsize()
    return status

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
    if S3_BUCKET:
        uvicorn.run(app, host="0.0.0.0", port=8000)
    else:
        uvicorn.run(app, host="127.0.0.1", port=8000)
