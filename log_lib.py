import os, csv
from datetime import datetime
from threading import Lock 

# create log directory
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
log_lock = Lock()

def get_log_name():
    # create log file with timestamp
    log_filename = datetime.now().strftime("log_%Y-%m-%d_%H-%M.log")
    log_path = os.path.join(LOG_DIR, log_filename)
    return log_path


def log(message, log_path=None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] {message}"

    # print to console
    if not log_path:
        print(log_message)
        return

    # write to file
    with log_lock:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_message + "\n")


def get_lead_profile_names():
    data = []
    folder_path = "attorney_profiles"

    for file_name in os.listdir(folder_path):
        if file_name.endswith(".csv"):
            file_path = os.path.join(folder_path, file_name)
            
            print(f"Reading: {file_name}")
            
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                reader = csv.reader(f)
                
                for row in reader:
                    data.append(row)

    # Print result
    profile_name_set = set()
    for row in data: # Optional: log duplicate profile names
        profile_name_set.add(row[0]) 
    
    return profile_name_set