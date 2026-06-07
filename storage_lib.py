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
AWS_ENDPOINT_URL            = os.environ.get("AWS_ENDPOINT_URL")  # custom endpoint for Oracle/MinIO/etc.
GCS_BUCKET_NAME             = os.environ.get("GCS_BUCKET_NAME")
AZURE_CONTAINER_NAME        = os.environ.get("AZURE_CONTAINER_NAME")
AZURE_STORAGE_CONNECTION_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
GOOGLE_DRIVE_FOLDER_ID      = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
GOOGLE_DRIVE_CREDENTIALS    = os.environ.get("GOOGLE_DRIVE_CREDENTIALS")  # service account JSON string

# ── lazy singletons ────────────────────────────────────────────────────────────
_s3_client        = None
_gcs_bucket       = None
_azure_container  = None


# ── backend detection ──────────────────────────────────────────────────────────

def get_backend() -> str:
    """Returns 's3', 'gcs', 'azure', 'gdrive', or 'local'."""
    if S3_BUCKET_NAME:
        return "s3"
    if GCS_BUCKET_NAME:
        return "gcs"
    if AZURE_CONTAINER_NAME and AZURE_STORAGE_CONNECTION_STRING:
        return "azure"
    if GOOGLE_DRIVE_FOLDER_ID and GOOGLE_DRIVE_CREDENTIALS:
        return "gdrive"
    return "local"


def is_cloud() -> bool:
    """True when any cloud backend is active."""
    return get_backend() != "local"


# ── S3 backend ─────────────────────────────────────────────────────────────────

def _s3():
    global _s3_client
    if _s3_client is None:
        import boto3
        kwargs = {"region_name": AWS_REGION}
        if AWS_ENDPOINT_URL:
            kwargs["endpoint_url"] = AWS_ENDPOINT_URL
        _s3_client = boto3.client("s3", **kwargs)
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


# ── Google Drive backend ──────────────────────────────────────────────────────

class _GoogleDriveBackend:
    def __init__(self):
        self._service = None
        self._folder_cache: dict = {}

    def _svc(self):
        if self._service is None:
            import json
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            creds = service_account.Credentials.from_service_account_info(
                json.loads(GOOGLE_DRIVE_CREDENTIALS),
                scopes=["https://www.googleapis.com/auth/drive"]
            )
            self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def _get_or_create_folder(self, svc, name: str, parent_id: str) -> str:
        key = f"{parent_id}/{name}"
        if key in self._folder_cache:
            return self._folder_cache[key]
        q = f"name='{name}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        res = svc.files().list(q=q, fields="files(id)").execute().get("files", [])
        if res:
            fid = res[0]["id"]
        else:
            fid = svc.files().create(
                body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
                fields="id"
            ).execute()["id"]
        self._folder_cache[key] = fid
        return fid

    def _resolve(self, svc, path: str, create: bool = False):
        """Return (parent_folder_id, filename). Returns (None, filename) if folder not found."""
        parts = path.replace("\\", "/").split("/")
        filename = parts[-1]
        current = GOOGLE_DRIVE_FOLDER_ID
        for part in parts[:-1]:
            if not part:
                continue
            key = f"{current}/{part}"
            if key in self._folder_cache:
                current = self._folder_cache[key]
            elif create:
                current = self._get_or_create_folder(svc, part, current)
            else:
                q = f"name='{part}' and '{current}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
                res = svc.files().list(q=q, fields="files(id)").execute().get("files", [])
                if not res:
                    return None, filename
                current = res[0]["id"]
                self._folder_cache[key] = current
        return current, filename

    def _find_file_id(self, svc, path: str):
        parent_id, filename = self._resolve(svc, path, create=False)
        if not parent_id:
            return None
        q = f"name='{filename}' and '{parent_id}' in parents and trashed=false"
        res = svc.files().list(q=q, fields="files(id)").execute().get("files", [])
        return res[0]["id"] if res else None

    def _list_recursive(self, svc, folder_id: str, path_prefix: str, out: list):
        token = None
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed=false",
                "fields": "nextPageToken, files(id, name, mimeType, modifiedTime)",
                "pageSize": 100,
            }
            if token:
                params["pageToken"] = token
            resp = svc.files().list(**params).execute()
            for item in resp.get("files", []):
                item_path = f"{path_prefix}/{item['name']}"
                if item["mimeType"] == "application/vnd.google-apps.folder":
                    self._list_recursive(svc, item["id"], item_path, out)
                elif item["name"].endswith(".csv"):
                    out.append({"Key": item_path, "LastModified": item["modifiedTime"]})
            token = resp.get("nextPageToken")
            if not token:
                break

    def verify_connection(self):
        svc = self._svc()
        svc.files().get(fileId=GOOGLE_DRIVE_FOLDER_ID, fields="id").execute()
        print(f"GDRIVE CONFIG SUCCESS: Connected to folder: {GOOGLE_DRIVE_FOLDER_ID}")

    def list_files(self, prefix: str, sort_by_time: bool = False) -> list:
        svc = self._svc()
        prefix_clean = prefix.rstrip("/")
        current = GOOGLE_DRIVE_FOLDER_ID
        for part in prefix_clean.split("/"):
            if not part:
                continue
            key = f"{current}/{part}"
            if key in self._folder_cache:
                current = self._folder_cache[key]
            else:
                q = f"name='{part}' and '{current}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
                res = svc.files().list(q=q, fields="files(id)").execute().get("files", [])
                if not res:
                    return []
                current = res[0]["id"]
                self._folder_cache[key] = current
        out: list = []
        self._list_recursive(svc, current, prefix_clean, out)
        if sort_by_time:
            out.sort(key=lambda x: x["LastModified"])
        return out

    def read_file(self, path: str) -> bytes:
        import io
        from googleapiclient.http import MediaIoBaseDownload
        svc = self._svc()
        file_id = self._find_file_id(svc, path)
        if not file_id:
            raise FileNotFoundError(f"Google Drive file not found: {path}")
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=file_id))
        done = False
        while not done:
            _, done = dl.next_chunk()
        return buf.getvalue()

    def write_file(self, path: str, content: bytes):
        import io
        from googleapiclient.http import MediaIoBaseUpload
        svc = self._svc()
        parent_id, filename = self._resolve(svc, path, create=True)
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype="text/csv", resumable=False)
        q = f"name='{filename}' and '{parent_id}' in parents and trashed=false"
        existing = svc.files().list(q=q, fields="files(id)").execute().get("files", [])
        if existing:
            svc.files().update(fileId=existing[0]["id"], media_body=media).execute()
        else:
            svc.files().create(
                body={"name": filename, "parents": [parent_id]},
                media_body=media, fields="id"
            ).execute()

    def delete_file(self, path: str):
        svc = self._svc()
        file_id = self._find_file_id(svc, path)
        if file_id:
            svc.files().delete(fileId=file_id).execute()

    def file_exists(self, path: str) -> bool:
        return self._find_file_id(self._svc(), path) is not None


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

_gdrive_backend = None

def _backend():
    global _gdrive_backend
    b = get_backend()
    if b == "s3":
        return _S3Backend()
    if b == "gcs":
        return _GCSBackend()
    if b == "azure":
        return _AzureBackend()
    if b == "gdrive":
        if _gdrive_backend is None:
            _gdrive_backend = _GoogleDriveBackend()
        return _gdrive_backend
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
