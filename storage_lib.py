"""
storage_lib.py — unified storage abstraction for four backends.

Backend selection (checked in order):
  1. S3    — set S3_BUCKET_NAME (+ AWS_REGION, AWS credentials via IAM / env)
  2. GCS   — set GCS_BUCKET_NAME (+ GOOGLE_APPLICATION_CREDENTIALS or ADC)
  3. Azure — set AZURE_CONTAINER_NAME + AZURE_STORAGE_CONNECTION_STRING
  4. Local filesystem — default when no cloud env var is set

Public API (import and call these directly):
    is_cloud()           -> bool
    get_backend()        -> "s3" | "gcs" | "azure" | "local"
    verify_connection()
    list_files(prefix, sort_by_time=False) -> [{"Key": str, "LastModified": ...}]
    read_file(path)      -> bytes          raises FileNotFoundError
    write_file(path, content: str | bytes)
    delete_file(path)
    file_exists(path)    -> bool
"""

import os
import glob as _glob
from pathlib import Path

# ── env config ────────────────────────────────────────────────────────────────
S3_BUCKET_NAME              = os.environ.get("S3_BUCKET_NAME")
AWS_REGION                  = os.environ.get("AWS_REGION", "ap-south-1")
GCS_BUCKET_NAME             = os.environ.get("GCS_BUCKET_NAME")
AZURE_CONTAINER_NAME        = os.environ.get("AZURE_CONTAINER_NAME")
AZURE_STORAGE_CONNECTION_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")

# ── lazy singletons ────────────────────────────────────────────────────────────
_s3_client        = None
_gcs_bucket       = None
_azure_container  = None


# ── backend detection ──────────────────────────────────────────────────────────

def get_backend() -> str:
    """Returns 's3', 'gcs', 'azure', or 'local'."""
    if S3_BUCKET_NAME:
        return "s3"
    if GCS_BUCKET_NAME:
        return "gcs"
    if AZURE_CONTAINER_NAME and AZURE_STORAGE_CONNECTION_STRING:
        return "azure"
    return "local"


def is_cloud() -> bool:
    """True when any cloud backend is active."""
    return get_backend() != "local"


# ── S3 backend ─────────────────────────────────────────────────────────────────

def _s3():
    global _s3_client
    if _s3_client is None:
        import boto3
        _s3_client = boto3.client("s3", region_name=AWS_REGION)
    return _s3_client


class _S3Backend:
    def verify_connection(self):
        _s3().list_objects_v2(Bucket=S3_BUCKET_NAME, MaxKeys=1)
        print(f"S3 CONFIG SUCCESS: Connected to bucket: {S3_BUCKET_NAME}")

    def list_files(self, prefix: str, sort_by_time: bool = False) -> list:
        resp = _s3().list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=prefix)
        contents = [obj for obj in resp.get("Contents", []) if obj["Key"].endswith(".csv")]
        if sort_by_time:
            contents.sort(key=lambda o: o["LastModified"])
        return [{"Key": obj["Key"], "LastModified": obj["LastModified"]} for obj in contents]

    def read_file(self, path: str) -> bytes:
        try:
            obj = _s3().get_object(Bucket=S3_BUCKET_NAME, Key=path)
            return obj["Body"].read()
        except _s3().exceptions.NoSuchKey:
            raise FileNotFoundError(f"S3 key not found: {path}")
        except Exception as exc:
            # Catch ClientError for NoSuchKey across boto3 versions
            if "NoSuchKey" in str(exc) or "404" in str(exc):
                raise FileNotFoundError(f"S3 key not found: {path}")
            raise

    def write_file(self, path: str, content: bytes):
        _s3().put_object(Bucket=S3_BUCKET_NAME, Key=path, Body=content)

    def delete_file(self, path: str):
        _s3().delete_object(Bucket=S3_BUCKET_NAME, Key=path)

    def file_exists(self, path: str) -> bool:
        try:
            _s3().head_object(Bucket=S3_BUCKET_NAME, Key=path)
            return True
        except Exception:
            return False


# ── GCS backend ────────────────────────────────────────────────────────────────

def _gcs():
    global _gcs_bucket
    if _gcs_bucket is None:
        from google.cloud import storage
        _gcs_bucket = storage.Client().bucket(GCS_BUCKET_NAME)
    return _gcs_bucket


class _GCSBackend:
    def verify_connection(self):
        list(_gcs().list_blobs(max_results=1))
        print(f"GCS CONFIG SUCCESS: Connected to bucket: {GCS_BUCKET_NAME}")

    def list_files(self, prefix: str, sort_by_time: bool = False) -> list:
        blobs = [b for b in _gcs().list_blobs(prefix=prefix) if b.name.endswith(".csv")]
        if sort_by_time:
            blobs.sort(key=lambda b: b.updated)
        return [{"Key": b.name, "LastModified": b.updated} for b in blobs]

    def read_file(self, path: str) -> bytes:
        from google.api_core.exceptions import NotFound
        try:
            return _gcs().blob(path).download_as_bytes()
        except NotFound:
            raise FileNotFoundError(f"GCS object not found: {path}")

    def write_file(self, path: str, content: bytes):
        _gcs().blob(path).upload_from_string(content, content_type="text/csv")

    def delete_file(self, path: str):
        from google.api_core.exceptions import NotFound
        try:
            _gcs().blob(path).delete()
        except NotFound:
            pass

    def file_exists(self, path: str) -> bool:
        return _gcs().blob(path).exists()


# ── Azure backend ─────────────────────────────────────────────────────────────

def _azure():
    global _azure_container
    if _azure_container is None:
        from azure.storage.blob import BlobServiceClient
        _azure_container = BlobServiceClient.from_connection_string(
            AZURE_STORAGE_CONNECTION_STRING
        ).get_container_client(AZURE_CONTAINER_NAME)
    return _azure_container


class _AzureBackend:
    def verify_connection(self):
        next(iter(_azure().list_blobs()), None)
        print(f"AZURE CONFIG SUCCESS: Connected to container: {AZURE_CONTAINER_NAME}")

    def list_files(self, prefix: str, sort_by_time: bool = False) -> list:
        blobs = [b for b in _azure().list_blobs(name_starts_with=prefix) if b["name"].endswith(".csv")]
        if sort_by_time:
            blobs.sort(key=lambda b: b["last_modified"])
        return [{"Key": b["name"], "LastModified": b["last_modified"]} for b in blobs]

    def read_file(self, path: str) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return _azure().get_blob_client(path).download_blob().readall()
        except ResourceNotFoundError:
            raise FileNotFoundError(f"Azure blob not found: {path}")

    def write_file(self, path: str, content: bytes):
        _azure().get_blob_client(path).upload_blob(content, overwrite=True)

    def delete_file(self, path: str):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            _azure().get_blob_client(path).delete_blob()
        except ResourceNotFoundError:
            pass

    def file_exists(self, path: str) -> bool:
        return _azure().get_blob_client(path).exists()


# ── Local backend ──────────────────────────────────────────────────────────────

class _LocalBackend:
    def verify_connection(self):
        pass  # local always works

    def list_files(self, prefix: str, sort_by_time: bool = False) -> list:
        prefix_clean = prefix.rstrip("/")
        files = _glob.glob(f"{prefix_clean}/**/*.csv", recursive=True)
        files = [f.replace("\\", "/") for f in files if os.path.isfile(f)]
        if sort_by_time:
            files.sort(key=os.path.getmtime)
        else:
            files.sort()
        return [{"Key": f, "LastModified": os.path.getmtime(f)} for f in files]

    def read_file(self, path: str) -> bytes:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Local file not found: {path}")
        with open(path, "rb") as f:
            return f.read()

    def write_file(self, path: str, content: bytes):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)

    def delete_file(self, path: str):
        if os.path.exists(path):
            os.remove(path)

    def file_exists(self, path: str) -> bool:
        return os.path.exists(path)


# ── active backend singleton ───────────────────────────────────────────────────

def _backend():
    b = get_backend()
    if b == "s3":
        return _S3Backend()
    if b == "gcs":
        return _GCSBackend()
    if b == "azure":
        return _AzureBackend()
    return _LocalBackend()


# ── public API ─────────────────────────────────────────────────────────────────

def verify_connection():
    _backend().verify_connection()


def list_files(prefix: str, sort_by_time: bool = False) -> list:
    return _backend().list_files(prefix, sort_by_time=sort_by_time)


def read_file(path: str) -> bytes:
    return _backend().read_file(path)


def write_file(path: str, content):
    if isinstance(content, str):
        content = content.encode("utf-8")
    _backend().write_file(path, content)


def delete_file(path: str):
    _backend().delete_file(path)


def file_exists(path: str) -> bool:
    return _backend().file_exists(path)
