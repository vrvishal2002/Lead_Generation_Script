import os, csv, io
from pathlib import Path
from datetime import datetime
from threading import Lock 
from urllib.parse import urlparse
import boto3

S3_BUCKET = os.environ.get("S3_BUCKET_NAME")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
s3_client = boto3.client('s3', region_name=AWS_REGION) if S3_BUCKET else None

# create log directory
LOG_DIR = "logs"
if not S3_BUCKET:
    os.makedirs(LOG_DIR, exist_ok=True)

log_lock = Lock()
csv_file_lock = Lock()


def get_log_name():
    # create log file with timestamp
    log_filename = datetime.now().strftime("log_%Y-%m-%d_%H-%M.log")
    log_path = os.path.join(LOG_DIR, log_filename)
    return log_path


def get_domain(url):
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    return domain


def log(message, log_path=None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] {message}"

    # print to console
    if not log_path:
        print(log_message)
        return

    print(log_message)
    
    if S3_BUCKET:
        try:
            # For S3, we usually append by reading existing or just writing new log lines
            # For efficiency in a scraper, we'll just print to console which ECS captures in CloudWatch
            pass 
        except: pass
    else:
        with log_lock:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(log_message + "\n")


def get_lead_profile_names():
    data = []
    folder_path = "attorney_profiles"

    if S3_BUCKET:
        try:
            response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=f"{folder_path}/")
            for obj in response.get('Contents', []):
                if obj['Key'].endswith('.csv'):
                    content = s3_client.get_object(Bucket=S3_BUCKET, Key=obj['Key'])['Body'].read().decode('utf-8')
                    reader = csv.reader(io.StringIO(content))
                    data.extend(list(reader))
        except: pass
        profile_name_set = {row[0] for row in data if row and len(row) > 0}
        return profile_name_set
    else:
        with csv_file_lock:
            for file_name in Path(folder_path).rglob("*.csv"):
                if file_name.suffix == ".csv":
                    print(f"Reading: {file_name}")
                    with open(file_name, encoding="utf-8", errors="ignore") as f:
                        reader = csv.reader(f)
                        for row in reader:
                            data.append(row)

    # Print result
    profile_name_set = set()
    for row in data: # Optional: log duplicate profile names
        profile_name_set.add(row[0]) 
    
    return profile_name_set


def get_domain_names():
    data = []
    folder_path = "attorney_profiles"

    if S3_BUCKET:
        try:
            response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=f"{folder_path}/")
            for obj in response.get('Contents', []):
                if obj['Key'].endswith('.csv'):
                    content = s3_client.get_object(Bucket=S3_BUCKET, Key=obj['Key'])['Body'].read().decode('utf-8')
                    reader = csv.reader(io.StringIO(content))
                    data.extend(list(reader))
        except Exception as e:
            print(f"S3 get_domain_names error: {e}")
        domain_set = {get_domain(row[3]) for row in data if row and len(row) > 3}
        return domain_set
    else:
        with csv_file_lock:
            for file_name in Path(folder_path).rglob("*.csv"):
                if file_name.suffix == ".csv":
                    
                    print(f"Reading: {file_name}")
                    
                    with open(file_name, encoding="utf-8", errors="ignore") as f:
                        reader = csv.reader(f)
                        
                        for row in reader:
                            data.append(row)
    


    # Print result
    domain_set = set()
    for row in data: # Optional: log duplicate profile names
        if row and len(row) > 3:
            domain_set.add(get_domain(row[3])) 
    
    return domain_set
