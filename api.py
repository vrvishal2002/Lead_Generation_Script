import threading
import uuid
import time
import os, json
import csv, io
from pathlib import Path
from contextlib import asynccontextmanager
from google import genai
import pandas as pd
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, File, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from lead_generation_helper import LeadGenerationHelper
from firm_parser import FirmParser
from log_lib import log, flush_log, get_domain_names, get_lead_profile_names, csv_file_lock
import storage_lib

# Configure Gemini AI
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "AIzaSyBNhQK0iONS8JYDrHSPPpbNNpwegaSZgHY")
ai_client = None
if GOOGLE_API_KEY:
    try:
        ai_client = genai.Client(api_key=GOOGLE_API_KEY)
    except Exception as e:
        log(f"AI Initialization Failed: {e}")

# Initialize Config Storage
CONFIG_DIR = Path("configs")
CONFIG_DIR.mkdir(exist_ok=True)

# Seed default attorney config if missing
def seed_configs():
    """Ensures the default attorney configuration exists."""
    if not (CONFIG_DIR / "attorney.json").exists():
        with open(CONFIG_DIR / "attorney.json", "w") as f:
            json.dump({
                "id": "attorney",
                "name": "Attorney Leads",
                "folder_name": "attorney_profiles",
                "file_prefix": "attorney",
                "default_query": "medical malpractice and personal injury lawyers in",
                "directory_keywords": [
                    "attorneys", "our-team", "team", "legal-team", "lawyers", "meet",
                    "profiles", "about", "people", "paralegals", "advocates"
                ],
                "profile_required_keywords": [
                    "personal injury", "medical malpractice", "medical negligence",
                    "wrongful death", "accident", "dog bite", "drug"
                ],
                "lead_role_keywords": [
                    "associate", "attorney", "advocate", "lawyer", "partner",
                    "paralegal", "legal", "esq", "of counsel", "senior", "principal"
                ],
                "slug_exclusion_keywords": [
                    "attorney", "lawyer", "profile", "legal", "contact",
                    "disclaimer", "privacy", "blog", "news"
                ],
                "disposable_exclusion": [
                    "mailinator.com", "tempmail.com", "10minutemail.com", "guerrillamail.com",
                    "protectingpatientrights.com", "cellinolaw.com", "cartermario.com",
                    "brandonjbroderick.com", "carmodylaw.com", "danaherlagnese.com",
                    "brownandcrouppen.com", "devaultlaw.com", "www.devaultlaw.com"
                ]
            }, f, indent=4)

seed_configs()

shutdown_event = threading.Event()

class LeadConfig(BaseModel):
    id: str = Field(..., description="Unique ID for the lead type")
    name: str = Field(..., description="Display name (e.g., Real Estate Leads)")
    folder_name: str = Field(..., description="Storage directory name")
    file_prefix: str = Field(..., description="Prefix for generated files")
    default_query: str = Field(..., description="Template for Google search")
    directory_keywords: List[str] = Field(default_factory=list)
    profile_required_keywords: List[str] = Field(default_factory=list)
    lead_role_keywords: List[str] = Field(default_factory=list)
    slug_exclusion_keywords: List[str] = Field(default_factory=list)
    disposable_exclusion: List[str] = Field(default_factory=list)

def is_safe_path(path: str) -> bool:
    """Validates that the path is within allowed directories."""
    base_dirs = [os.path.abspath("Firms_details")]

    # Dynamically allow all folders defined in configs
    for cfg_file in CONFIG_DIR.glob("*.json"):
        try:
            with open(cfg_file, 'r') as f:
                cfg = json.load(f)
                folder = cfg.get("folder_name")
                if folder:
                    base_dirs.append(os.path.abspath(folder))
                    base_dirs.append(os.path.abspath(os.path.join(folder, "saved")))
                    base_dirs.append(os.path.abspath(os.path.join(folder, "uploaded")))
                    base_dirs.append(os.path.abspath(os.path.join(folder, "consolidated")))
        except: continue

    try:
        target_path = os.path.abspath(path)
        return any(target_path.startswith(d) for d in base_dirs)
    except Exception:
        return False

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify cloud storage connection if configured
    if storage_lib.is_cloud():
        try:
            storage_lib.verify_connection()
        except Exception as e:
            backend = storage_lib.get_backend().upper()
            print(f"{backend} CONFIG ERROR: Could not connect to storage. Check credentials and bucket name.")
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
    return {
        "status": "healthy",
        "active_jobs": len(active_jobs),
        "storage_backend": storage_lib.get_backend()
    }

@app.get("/configs")
async def list_configs():
    """Lists all available lead generation configurations."""
    seed_configs()
    configs = []
    for cfg_file in CONFIG_DIR.glob("*.json"):
        try:
            with open(cfg_file, 'r') as f:
                configs.append(json.load(f))
        except Exception as e:
            log(f"Error loading config {cfg_file}: {str(e)}")
    return configs

@app.post("/configs")
async def save_config(config: LeadConfig):
    """Saves a new lead generation configuration."""
    file_path = CONFIG_DIR / f"{config.id}.json"
    try:
        with open(file_path, 'w') as f:
            json.dump(config.dict(), f, indent=4)
        return {"message": f"Config '{config.name}' saved successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/configs/{config_id}")
async def delete_config(config_id: str):
    """Deletes a lead generation configuration."""
    file_path = CONFIG_DIR / f"{config_id}.json"
    if file_path.exists():
        os.remove(file_path)
        return {"message": f"Config '{config_id}' deleted successfully."}
    raise HTTPException(status_code=404, detail="Config not found")

@app.post("/configs/reset-default")
async def reset_default_config():
    """Restores the hardcoded attorney default configuration."""
    file_path = CONFIG_DIR / "attorney.json"
    if file_path.exists():
        os.remove(file_path)
    seed_configs()
    return {"message": "Attorney configuration reset to default values."}

@app.post("/ai/suggest-config")
async def ai_suggest_config(description: str = Query(...)):
    """AI Logic to suggest configuration based on user requirement."""
    slug = description.lower().replace(" ", "_")[:10]
    fallback = {
        "id": slug, "name": f"{description.title()} Leads",
        "folder_name": f"{slug}_profiles", "file_prefix": slug[:5],
        "default_query": f"best {description} companies in",
        "directory_keywords": ["team", "about", "staff", "people", "profiles"],
        "profile_required_keywords": [description.split()[0]] if description else [],
        "lead_role_keywords": ["owner", "manager", "founder", "director"],
        "slug_exclusion_keywords": ["contact", "privacy", "terms"],
        "disposable_exclusion": ["tempmail.com", "mailinator.com"],
        "_is_fallback": True
    }

    if not ai_client:
        return fallback

    try:
        prompt = f"""
    Act as a Lead Generation Expert. Suggest a JSON configuration for sourcing leads in this niche: "{description}".
    Return ONLY a valid JSON object with these keys:
    "id" (short slug), "name" (Display Name), "folder_name" (id + _profiles),
    "file_prefix" (3-5 chars), "default_query" (Google search query ending with 'in'),
    "directory_keywords" (List: common URL slugs like 'team', 'staff'),
    "profile_required_keywords" (List: words that must appear in a profile to be valid),
    "lead_role_keywords" (List: job titles to search for),
    "slug_exclusion_keywords" (List: URL parts to ignore),
    "disposable_exclusion" (List: email domains to skip).
    """
        models_to_try = ['gemini-2.5-flash', 'gemini-flash-latest', 'gemma-3-27b-it']
        response = None
        used_model = "None"

        for model_id in models_to_try:
            try:
                response = ai_client.models.generate_content(model=model_id, contents=prompt)
                if response and response.text:
                    used_model = model_id
                    break
            except Exception as e:
                err_msg = str(e)
                log(f"AI Model {model_id} failed: {err_msg}")
                continue

        if response and response.text:
            clean_json = response.text.strip().replace("```json", "").replace("```", "")
            config_data = json.loads(clean_json)
            config_data["_ai_model"] = used_model
            return config_data
        else:
            raise Exception("All AI models failed to respond.")
    except Exception as e:
        log(f"AI Suggestion Error (Falling back to heuristics): {str(e)}")
        fallback["_error_reason"] = str(e)
        return fallback

@app.post("/ai/suggest-field")
async def ai_suggest_field(field: str = Query(...), description: str = Query(...)):
    """AI Logic to suggest a specific field value based on niche description."""
    if not ai_client:
        return ["AI disabled: Set GOOGLE_API_KEY"]

    try:
        prompt = f"Given the niche '{description}', provide a comma-separated list of values for the configuration field '{field}' used in lead scraping. Return only the values."
        models_to_try = ['gemini-2.5-flash', 'gemini-flash-latest', 'gemma-3-27b-it']
        response = None
        used_model = "None"

        for model_id in models_to_try:
            try:
                response = ai_client.models.generate_content(model=model_id, contents=prompt)
                if response and response.text:
                    used_model = model_id
                    break
            except Exception as e:
                log(f"AI Field Model {model_id} failed: {e}")
                continue

        if response and response.text:
            values = [v.strip() for v in response.text.split(",")]
            return {"values": values, "model": used_model}
        else:
            raise Exception("AI could not generate field values.")
    except Exception as e:
        log(f"AI Field Suggestion Error: {str(e)}")
        return {"values": ["Heuristic Error: Try describing the niche differently"], "model": "None"}

@app.get("/metrics/overall")
async def get_overall_metrics():
    """Aggregates metrics across all lead types/configs."""
    configs = await list_configs()
    total_leads = 0
    niche_stats = []

    for cfg in configs:
        folder = cfg["folder_name"]
        files_info = storage_lib.list_files(f"{folder}/")
        leads_in_niche = len(files_info)
        total_leads += leads_in_niche
        niche_stats.append({"name": cfg["name"], "files": leads_in_niche})

    return {
        "total_niches": len(configs),
        "approx_total_files": total_leads,
        "niche_breakdown": niche_stats
    }

@app.delete("/results/delete")
async def delete_result_file(path: str = Query(...), password: str = Query(...)):
    """Deletes a file if the password is correct."""
    if password != "admin123":
        raise HTTPException(status_code=403, detail="Invalid password.")

    try:
        if not storage_lib.is_cloud() and (not is_safe_path(path) or not os.path.exists(path)):
            raise HTTPException(status_code=404, detail="File not found")
        storage_lib.delete_file(path)
        return {"message": "File deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/results")
async def list_results(folder: str = Query(default="attorney_profiles")):
    """Lists all CSV files found in the specified profiles directory."""
    results = {}
    files_info = storage_lib.list_files(f"{folder}/", sort_by_time=True)
    files = [obj["Key"] for obj in files_info]
    # Exclude sub-folders used for saved/uploaded/consolidated artefacts
    files = [f for f in files if all(x not in f for x in ["uploaded/", "consolidated/", "saved/"])]

    for f in files:
        f_clean = f.replace("\\", "/")
        parts = f_clean.split("/")
        state = parts[1] if len(parts) > 2 else "Uncategorized"
        if state not in results:
            results[state] = []
        results[state].append(f_clean)

    return results


def get_file(path: str):
    """Returns a readable file-like object (StringIO or open file handle)."""
    if storage_lib.is_cloud():
        try:
            content = storage_lib.read_file(path)
            return io.StringIO(content.decode("utf-8", errors="ignore"))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="File not found in cloud storage")
    else:
        if not is_safe_path(path) or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="File not found")
        return open(path, encoding="utf-8", errors="ignore")


@app.get("/results/view")
async def view_result(path: str = Query(...)):
    """Reads a CSV file and returns the data as JSON."""
    try:
        f = get_file(path)
        ui_headers = ["S.No", "Name", "Phone", "Email", "Profile URL", "Company", "City", "State", "Verified By"]
        data = []

        with csv_file_lock:
            reader = csv.DictReader(f)

            for row in reader:
                entry = {}
                entry["S.No"] = len(data) + 1
                entry["Name"] = row.get("Name", "")
                entry["Phone"] = row.get("Phone", "")
                entry["Email"] = row.get("Email", "")
                entry["Profile URL"] = row.get("Profile URL", "")
                entry["Company"] = row.get("Company", row.get("Firm Name", ""))
                entry["City"] = row.get("City", "")
                entry["State"] = row.get("State", "")
                entry["Verified By"] = row.get("Verified By", "verified by smtp")
                data.append(entry)

            f.close()

            if data:
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(ui_headers[1:])
                for row in data:
                    writer.writerow(list(row.values())[1:])
                storage_lib.write_file(path, output.getvalue())

        return data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/results/download")
async def download_result(path: str = Query(...)):
    """Serves the CSV file for download."""
    if storage_lib.is_cloud():
        try:
            content = storage_lib.read_file(path)
            return StreamingResponse(
                io.BytesIO(content),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={os.path.basename(path)}"}
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="File not found in cloud storage")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        if not is_safe_path(path) or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(path, filename=os.path.basename(path))

@app.get("/results/uploaded")
async def list_uploaded(folder: str = Query(default="attorney_profiles")):
    """Lists all CSV files found in the specified uploaded directory."""
    files_info = storage_lib.list_files(f"{folder}/uploaded/")
    return {"files": [obj["Key"] for obj in files_info]}

@app.post("/results/upload")
async def upload_leads(file: UploadFile = File(...), folder: str = Query(default="attorney_profiles")):
    """Uploads a leads CSV file, validates its structure, and saves it."""
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are allowed.")

    upload_path = None
    try:
        content = await file.read()

        # Determine upload path for both cloud and local
        if storage_lib.is_cloud():
            upload_path = f"{folder}/uploaded/{file.filename}"
        else:
            upload_dir = Path(folder) / "uploaded"
            upload_dir.mkdir(parents=True, exist_ok=True)
            upload_path = str(upload_dir / file.filename).replace("\\", "/")

        storage_lib.write_file(upload_path, content)

        # Validate CSV content
        if not content or len(content.strip()) == 0:
            raise HTTPException(status_code=400, detail="The uploaded CSV file is empty.")

        df = pd.read_csv(io.BytesIO(content))

        all_cols = ["Name", "Phone", "Email", "Profile URL", "Company", "City", "State", "Verified By"]
        compulsory_cols = ["Name", "Email", "Profile URL", "Company", "City", "State", "Verified By"]

        missing_headers = [col for col in all_cols if col not in df.columns]
        if missing_headers:
            storage_lib.delete_file(upload_path)
            raise HTTPException(status_code=400, detail=f"Invalid structure. Missing columns: {', '.join(missing_headers)}")

        is_empty = df[compulsory_cols].isna() | (df[compulsory_cols].astype(str).apply(lambda x: x.str.strip()) == "")
        if is_empty.any().any():
            storage_lib.delete_file(upload_path)
            bad_rows = is_empty.any(axis=1)
            bad_cols = is_empty.columns[is_empty.any(axis=0)].tolist()
            sample_rows = (df[bad_rows].index[:5] + 2).tolist()  # +2: 1 for header, 1 for 1-based
            raise HTTPException(status_code=400, detail=(
                f"Validation failed. Empty values found in mandatory columns: {', '.join(bad_cols)}. "
                f"First affected rows (CSV line numbers): {sample_rows}. "
                f"Total affected rows: {int(bad_rows.sum())}."
            ))

        return {"message": f"File '{file.filename}' uploaded successfully.", "rows": len(df)}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        if upload_path:
            try: storage_lib.delete_file(upload_path)
            except: pass
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/results/consolidated")
async def list_consolidated(folder: str = Query(default="attorney_profiles")):
    """Lists all CSV files found in the specified consolidated directory."""
    files_info = storage_lib.list_files(f"{folder}/consolidated/")
    return {"files": [obj["Key"] for obj in files_info]}

@app.get("/results/saved")
async def list_saved(folder: str = Query(default="attorney_profiles")):
    """Lists all CSV files in the specified saved directory."""
    files_info = storage_lib.list_files(f"{folder}/saved/", sort_by_time=True)
    return {"files": [obj["Key"] for obj in files_info]}

@app.post("/jobs/{job_id}/save")
async def save_job_results(job_id: str):
    """Saves all currently generated leads for a job into a single file."""
    if job_id not in active_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    helper = active_jobs[job_id]
    leads = helper.status.get("recent_leads", [])
    folder = helper.config.get("folder_name", "attorney_profiles")
    if not leads:
        return {"message": "No leads found to save yet."}

    df = pd.DataFrame(leads)
    filename = f"job_result_{helper.state}_{int(time.time())}.csv"
    save_path = f"{folder}/saved/{filename}"

    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    storage_lib.write_file(save_path, csv_buffer.getvalue())

    return {"message": f"Saved {len(leads)} leads to {filename}"}

class ConsolidateRequest(BaseModel):
    filename: str = "attorney_profiles_master"
    include_uploaded: bool = False
    folder: str = "attorney_profiles"

class ConsolidateUploadedRequest(BaseModel):
    filename: str = "uploaded_profiles_master"
    folder: str = "attorney_profiles"

@app.post("/results/consolidate-uploaded")
async def consolidate_uploaded(request: ConsolidateUploadedRequest):
    """Merges all uploaded CSV files into a master file saved in consolidated/."""
    folder_path = request.folder
    files_info = storage_lib.list_files(f"{folder_path}/uploaded/")
    files_to_process = [obj["Key"].replace("\\", "/") for obj in files_info]

    if not files_to_process:
        return {"message": "No uploaded files found to consolidate."}

    data = []
    for f_path in files_to_process:
        with csv_file_lock:
            with get_file(f_path) as f:
                reader = csv.reader(f)
                file_rows = list(reader)
                if not file_rows:
                    continue
                start_idx = 1 if file_rows[0] and file_rows[0][0] == "Name" else 0
                data.extend(file_rows[start_idx:])

    email_set = set()
    consolidated_data = []
    for row in data:
        if not row or len(row) < 3:
            continue
        email = row[2].strip().lower()
        if email not in email_set:
            consolidated_data.append(row)
            if email:
                email_set.add(email)

    header = ["Name", "Phone", "Email", "Profile URL", "Company", "City", "State", "Verified By"]
    clean_name = request.filename.replace(".csv", "") + ".csv"
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    for row in consolidated_data:
        row_to_write = list(row) + [""] * (len(header) - len(row))
        writer.writerow(row_to_write[:len(header)])

    storage_lib.write_file(f"{folder_path}/consolidated/{clean_name}", output.getvalue())
    return {
        "message": f"Consolidated {len(files_to_process)} uploaded files into {clean_name}. Total unique profiles: {len(consolidated_data)}."
    }

@app.post("/results/consolidate")
async def consolidate_results(request: ConsolidateRequest):
    """Merges CSV files into a master file with custom naming and optional upload inclusion."""
    folder_path = request.folder
    files_info = storage_lib.list_files(f"{folder_path}/")

    files_to_process = []
    for obj in files_info:
        key = obj["Key"].replace("\\", "/")
        parts = key.split("/")
        if all(x not in parts for x in ["uploaded", "consolidated", "saved"]):
            files_to_process.append(key)
        elif request.include_uploaded and "uploaded" in parts:
            files_to_process.append(key)

    if not files_to_process:
        return {"message": "No files found to consolidate."}

    data = []
    for f_path in files_to_process:
        with csv_file_lock:
            with get_file(f_path) as f:
                reader = csv.reader(f)
                file_rows = list(reader)
                if not file_rows: continue
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

    storage_lib.write_file(f"{folder_path}/consolidated/{clean_name}", output.getvalue())

    return {"message": f"Consolidated {len(files_to_process)} files into {clean_name}. Total unique leads: {len(consolidated_data)}."}

# In-memory storage for active jobs. In production, use Redis or a DB.
active_jobs: Dict[str, LeadGenerationHelper] = {}

class ScrapeRequest(BaseModel):
    state: str
    cities: List[str] = []
    base_query: str = "medical malpractice and personal injury lawyers"
    target: int = 10
    config_id: str = "attorney"

@app.on_event("startup")
async def startup_event():
    pass

@app.on_event("shutdown")
async def shutdown_event_handler():
    log("Shutdown signal received. Cleaning up jobs...")
    shutdown_event.set()


def run_job_logic(job_id: str, request: ScrapeRequest):
    """Replicates the main loop and run_city logic from lead_generation_script_2.py"""
    helper = active_jobs[job_id]

    # Load configuration
    config_path = CONFIG_DIR / f"{request.config_id}.json"
    if config_path.exists():
        with open(config_path, 'r') as f:
            helper.config = json.load(f)

    helper.status["total_cities"] = len(request.cities)
    helper.status["phase"] = "Initializing workers"
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

    # Start worker threads
    threads = []
    for _ in range(4):
        threads.append(threading.Thread(target=helper.firm_worker, daemon=True))

    threads.append(threading.Thread(target=helper.csv_writer, daemon=True))
    threads.append(threading.Thread(target=helper.monitor, daemon=True))

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
            discovery_progress = min(100, (count / target) * 100) * 0.4
            helper.status["cities"][city]["progress"] = round(discovery_progress, 1)

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

        # Save firms detail via storage abstraction
        file_key = f"Firms_details/google_places_firms_{city}_{request.state}.csv"
        try:
            csv_buffer = io.StringIO()
            pd.DataFrame(firms).to_csv(csv_buffer, index=False)
            storage_lib.write_file(file_key, csv_buffer.getvalue())
        except Exception as e:
            log(f"Job {job_id} — failed to save firms CSV for {city}: {e}", log_path)

        helper.status["cities"][city]["phase"] = "Extracting Leads"
        helper.status["firms_total"] = len(firms)

        start_leads = helper.status["profiles_found"]
        for firm in firms:
            if helper.cancel_event.is_set(): break
            helper.firm_queue.put(firm)

        wait_for_queue(helper.firm_queue)
        if shutdown_event.is_set() or helper.cancel_event.is_set(): break

        wait_for_queue(helper.profile_queue)
        if shutdown_event.is_set() or helper.cancel_event.is_set(): break

        wait_for_queue(helper.result_queue)
        if shutdown_event.is_set() or helper.cancel_event.is_set(): break

        helper.status["cities"][city]["leads_found"] = helper.status["profiles_found"] - start_leads
        helper.status["cities"][city]["phase"] = "Done"
        helper.status["cities"][city]["progress"] = 100

        with helper.domain_with_no_profiles_cache_lock:
            for domain, has_no_profiles in helper.domain_with_no_profiles_cache.items():
                if has_no_profiles:
                    domains_with_no_leads.add(domain)

        helper.status["cities_completed"] += 1
        helper.status["overall_progress"] = round((helper.status["cities_completed"] / len(request.cities)) * 100, 2)

        # Checkpoint: save this city's leads immediately so progress survives a crash,
        # then clear them from recent_leads to prevent unbounded memory growth (OOM).
        city_folder = helper.config.get("folder_name", "attorney_profiles")
        city_leads = [l for l in helper.status["recent_leads"] if l.get("City") == city]
        if city_leads:
            try:
                city_safe = city.replace(" ", "_").replace("(", "").replace(")", "").replace(",", "")
                chk_path = f"{city_folder}/saved/checkpoint_{helper.state}_{city_safe}_{job_id[:8]}.csv"
                buf = io.StringIO()
                pd.DataFrame(city_leads).to_csv(buf, index=False)
                storage_lib.write_file(chk_path, buf.getvalue())
                log(f"Checkpoint: saved {len(city_leads)} leads for {city} → {chk_path}", log_path)
            except Exception as chk_err:
                log(f"Checkpoint save failed for {city}: {chk_err}", log_path)
        helper.status["recent_leads"] = [l for l in helper.status["recent_leads"] if l.get("City") != city]

    except Exception as e:
        helper.status["phase"] = "Error"
        log(f"Job {job_id} encountered error: {e}", log_path)
    finally:
        if helper.cancel_event.is_set():
            helper.status["phase"] = "Terminating... Cleaning Up"
        else:
            helper.status["phase"] = "Finishing... Cleaning Up"

        for _ in range(4):
            helper.firm_queue.put(None)

        for _ in range(10):
            helper.profile_queue.put(None)

        helper.monitor_queue.put(None)

        wait_for_queue(helper.firm_queue)
        wait_for_queue(helper.profile_queue)

        helper.result_queue.put(None)
        wait_for_queue(helper.result_queue)

        if hasattr(helper, 'threads'):
            for t in helper.threads:
                t.join(timeout=1)

        if helper.cancel_event.is_set():
            helper.status["phase"] = "Cancelled"
        elif helper.status["phase"] != "Error":
            helper.status["phase"] = "Completed"

        # Auto-save results on completion or termination
        leads = helper.status.get("recent_leads", [])
        folder = helper.config.get("folder_name", "attorney_profiles")
        if leads:
            try:
                df = pd.DataFrame(leads)
                filename = f"auto_save_{helper.state}_{job_id[:8]}_{int(time.time())}.csv"
                save_path = f"{folder}/saved/{filename}"
                csv_buffer = io.StringIO()
                df.to_csv(csv_buffer, index=False)
                storage_lib.write_file(save_path, csv_buffer.getvalue())
                log(f"Auto-saved {len(leads)} leads to {filename}", log_path)
            except Exception as e:
                log(f"Auto-save failed for job {job_id}: {e}", log_path)

    log(f"Job {job_id} finished. Phase: {helper.status['phase']}", log_path)
    flush_log(log_path)
    time.sleep(5)
    active_jobs.pop(job_id, None)

@app.post("/jobs/start")
async def start_job(request: ScrapeRequest, background_tasks: BackgroundTasks):
    if any(j.state == request.state for j in active_jobs.values()):
        raise HTTPException(status_code=400, detail="A job for this state is already running.")

    config_path = CONFIG_DIR / f"{request.config_id}.json"
    config = None
    if config_path.exists():
        with open(config_path, 'r') as f:
            config = json.load(f)

    job_id = str(uuid.uuid4())
    helper = LeadGenerationHelper(state=request.state, config=config)

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

    for _ in range(10): helper.firm_queue.put(None)
    for _ in range(20): helper.profile_queue.put(None)
    helper.result_queue.put(None)

    return {"message": "Cancellation request received."}

@app.get("/logs")
async def list_logs():
    """Lists all log files available in storage, newest first."""
    try:
        files = storage_lib.list_files("logs/", sort_by_time=True)
        return [{"name": f["Key"], "last_modified": str(f.get("LastModified", ""))} for f in reversed(files)]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/logs/latest")
async def get_latest_log(tail: int = Query(default=200)):
    """Returns the last `tail` lines of the most recent log file."""
    try:
        files = storage_lib.list_files("logs/", sort_by_time=True)
        if not files:
            raise HTTPException(status_code=404, detail="No log files found")
        latest = files[-1]["Key"]
        content = storage_lib.read_file(latest).decode("utf-8", errors="ignore")
        lines = content.splitlines()
        return {"file": latest, "lines": lines[-tail:]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/logs/view")
async def view_log(name: str = Query(...), tail: int = Query(default=300)):
    """Returns the last `tail` lines of a specific log file."""
    try:
        content = storage_lib.read_file(name).decode("utf-8", errors="ignore")
        lines = content.splitlines()
        return {"file": name, "lines": lines[-tail:]}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Log file not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    # Bind to all interfaces when running in cloud, localhost-only otherwise
    host = "0.0.0.0" if storage_lib.is_cloud() else "127.0.0.1"
    uvicorn.run(app, host=host, port=8000)
