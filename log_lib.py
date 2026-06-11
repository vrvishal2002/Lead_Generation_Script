import os, csv, io
from pathlib import Path
from datetime import datetime
from threading import Lock
from urllib.parse import urlparse
import storage_lib

LOG_DIR = "logs"
if not storage_lib.is_cloud():
    os.makedirs(LOG_DIR, exist_ok=True)

log_lock = Lock()
csv_file_lock = Lock()

# Cloud log buffering: batch writes instead of one S3 round-trip per line.
# Reduces I/O by _FLUSH_SIZE× and eliminates most log_lock contention.
_FLUSH_SIZE = 25
_buffers: dict = {}          # {log_path: [line, ...]}
_buffers_lock = Lock()
_write_locks: dict = {}      # {log_path: Lock}  — one write lock per log file
_write_locks_meta = Lock()


def _get_write_lock(log_path: str) -> Lock:
    with _write_locks_meta:
        if log_path not in _write_locks:
            _write_locks[log_path] = Lock()
        return _write_locks[log_path]


def _flush(log_path: str, lines: list):
    """Write a batch of lines to cloud storage. Called outside _buffers_lock."""
    with _get_write_lock(log_path):
        try:
            try:
                existing = storage_lib.read_file(log_path).decode("utf-8")
            except FileNotFoundError:
                existing = ""
            storage_lib.write_file(log_path, existing + "\n".join(lines) + "\n")
        except Exception as e:
            print(f"[{storage_lib.get_backend().upper()}] Log flush error for {log_path}: {e}")


def flush_log(log_path: str):
    """Force-flush any buffered lines — call this at job end."""
    if not storage_lib.is_cloud() or not log_path:
        return
    with _buffers_lock:
        lines = _buffers.pop(log_path, None)
    if lines:
        _flush(log_path, lines)


def get_log_name():
    log_filename = datetime.now().strftime("log_%Y-%m-%d_%H-%M.log")
    return os.path.join(LOG_DIR, log_filename)


def get_domain(url):
    parsed = urlparse(url)
    return parsed.netloc.replace("www.", "")


def log(message, log_path=None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_message = f"[{timestamp}] {message}"

    print(log_message)

    if not log_path:
        return

    if storage_lib.is_cloud():
        to_flush = None
        with _buffers_lock:
            if log_path not in _buffers:
                _buffers[log_path] = []
            _buffers[log_path].append(log_message)
            if len(_buffers[log_path]) >= _FLUSH_SIZE:
                to_flush = _buffers.pop(log_path)
        if to_flush:
            _flush(log_path, to_flush)
    else:
        with log_lock:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(log_message + "\n")


def get_lead_profile_names():
    data = []
    folder_path = "attorney_profiles"

    if storage_lib.is_cloud():
        try:
            for obj in storage_lib.list_files(f"{folder_path}/"):
                content = storage_lib.read_file(obj["Key"]).decode("utf-8", errors="ignore")
                data.extend(csv.reader(io.StringIO(content)))
        except Exception as e:
            print(f"[{storage_lib.get_backend().upper()}] get_lead_profile_names error: {e}")
    else:
        with csv_file_lock:
            for file_name in Path(folder_path).rglob("*.csv"):
                print(f"Reading: {file_name}")
                with open(file_name, encoding="utf-8", errors="ignore") as f:
                    data.extend(csv.reader(f))

    return {row[0] for row in data if row}


def get_domain_names():
    data = []
    folder_path = "attorney_profiles"

    if storage_lib.is_cloud():
        try:
            for obj in storage_lib.list_files(f"{folder_path}/"):
                content = storage_lib.read_file(obj["Key"]).decode("utf-8", errors="ignore")
                data.extend(csv.reader(io.StringIO(content)))
        except Exception as e:
            print(f"[{storage_lib.get_backend().upper()}] get_domain_names error: {e}")
    else:
        with csv_file_lock:
            for file_name in Path(folder_path).rglob("*.csv"):
                print(f"Reading: {file_name}")
                with open(file_name, encoding="utf-8", errors="ignore") as f:
                    data.extend(csv.reader(f))

    return {get_domain(row[3]) for row in data if row and len(row) > 3}
