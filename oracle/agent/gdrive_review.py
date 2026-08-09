"""Drive review hub — deliverables become real Google Docs, not markdown squints.

Markdown is a fine storage format and a poor reading surface. Drive's upload API
converts markdown to a native Google Doc on the way in (set the target mimeType),
so a drafting run's output can land in a shared "review" folder as a formatted,
commentable document — readable on any phone — with binary assets (PDF/PNG/GIF)
uploaded alongside as themselves.

Configuration (both required, else uploads are skipped silently by callers):
  GDRIVE_KEY           — service-account key (keychain:gdrive-key supported)
  GDRIVE_REVIEW_FOLDER — id of a Drive folder shared to the service account as
                         EDITOR (the account can only reach what's shared to it)

Raw REST like the loader — no extra client library.
"""
import json
import mimetypes
import os
import pathlib

UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"
GDOC_MIME = "application/vnd.google-apps.document"


def _session():
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    key = os.environ.get("GDRIVE_KEY", "")
    if not key:
        raise RuntimeError("GDRIVE_KEY not configured")
    scopes = ["https://www.googleapis.com/auth/drive"]
    if key.lstrip().startswith("{"):
        creds = service_account.Credentials.from_service_account_info(
            json.loads(key), scopes=scopes)
    else:
        creds = service_account.Credentials.from_service_account_file(key, scopes=scopes)
    return AuthorizedSession(creds)


def enabled() -> bool:
    return bool(os.environ.get("GDRIVE_KEY") and os.environ.get("GDRIVE_REVIEW_FOLDER"))


def _multipart(metadata: dict, content: bytes, content_type: str):
    boundary = "----brainreview4c81"
    body = (f"--{boundary}\r\ncontent-type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\ncontent-type: {content_type}\r\n\r\n").encode() \
        + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/related; boundary={boundary}"


def upload(path, folder_id=None, title=None) -> str:
    """Upload one file into the review folder; .md converts to a Google Doc.
    Returns the file's web link."""
    p = pathlib.Path(path)
    folder = folder_id or os.environ.get("GDRIVE_REVIEW_FOLDER", "")
    if not folder:
        raise RuntimeError("GDRIVE_REVIEW_FOLDER not configured")
    is_md = p.suffix.lower() in (".md", ".markdown")
    meta = {"name": title or (p.stem if is_md else p.name), "parents": [folder]}
    if is_md:
        meta["mimeType"] = GDOC_MIME
        src_type = "text/markdown"
    else:
        src_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    body, ctype = _multipart(meta, p.read_bytes(), src_type)
    sess = _session()
    r = sess.post(f"{UPLOAD_API}?uploadType=multipart&supportsAllDrives=true"
                  f"&fields=id,webViewLink",
                  data=body, headers={"content-type": ctype}, timeout=120)
    r.raise_for_status()
    out = r.json()
    return out.get("webViewLink") or f"https://drive.google.com/file/d/{out['id']}/view"
