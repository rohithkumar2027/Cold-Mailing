"""
FastAPI Web Dashboard Backend for Recruiter Lead Generator & Cold Mailing Automation
"""
import os
import sys
from pathlib import Path
from typing import List, Optional

# Reconfigure Windows console streams for UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from modules.scraper import find_recruiters_for_company
from modules.email_finder import probe_contactout, key_id as _key_id, find_recruiter_email, get_key_status, CONTACTOUT_POOL, CONTACTOUT_CREDITS_PER_KEY, CONTACTOUT_CREDITS_PER_EMAIL
import re
from modules.sheets_manager import SheetsManager
from modules.email_templates import generate_email_draft
from config.settings import DEFAULT_GOOGLE_SHEET_NAME, DATA_DIR, BASE_DIR

app = FastAPI(title="AutoRecruit Lead Generator API")
sheets_manager = SheetsManager()

WEB_DIR = BASE_DIR / "web"


class SearchRequest(BaseModel):
    company: str
    role_category: str = "swe"
    location: str = "India"
    limit: int = 25
    depth: str = "deep"
    tech_only: bool = False
    verified_only: bool = True
    contactout_key: Optional[str] = None
    apollo_key: Optional[str] = None


class EmailRequest(BaseModel):
    name: str
    company: str
    linkedin_url: str = ""
    snippet: str = ""
    use_paid: bool = True     # allow spending ContactOut / Apollo credits when no public email is found


class KeysAddRequest(BaseModel):
    keys: str                 # one key, or several separated by spaces / commas / new lines


class SyncRequest(BaseModel):
    leads: List[dict]
    webhook_url: Optional[str] = None
    batch: bool = True   # False = only update the master CSV (used after Find emails)


class EmailDraftRequest(BaseModel):
    recruiter_name: str
    company: str
    role_category: str = "swe"
    candidate_name: Optional[str] = "Your Name"


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = WEB_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend index.html not found")
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))


@app.get("/api/status")
async def get_status():
    global sheets_manager
    return {
        "sheets_connected": sheets_manager.is_connected,
        "sheet_name": sheets_manager.sheet_name,
        "connection_error": sheets_manager.connection_error,
    }


@app.post("/api/search")
async def search_leads(req: SearchRequest):
    company = req.company.strip()
    if not company:
        raise HTTPException(status_code=400, detail="Company name cannot be empty")

    try:
        leads = await run_in_threadpool(
            find_recruiters_for_company,
            company=company,
            role_category=req.role_category,
            max_results=req.limit,
            location=req.location,
            depth=req.depth,
            tech_only=req.tech_only,
            verified_only=req.verified_only,
        )

        return {
            "success": True,
            "company": company,
            "role_category": req.role_category,
            "leads": leads,
            "count": len(leads),
        }
    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "leads": [],
        }


@app.get("/api/keys-status")
async def keys_status():
    """How many ContactOut / Apollo keys are loaded and still usable (never the keys themselves)."""
    return get_key_status()


def _keys_payload():
    return {
        "keys": CONTACTOUT_POOL.snapshot(),          # masked ids only - full keys never leave the server
        "summary": CONTACTOUT_POOL.status(),
        "credits_per_key": CONTACTOUT_CREDITS_PER_KEY,
        "cost_per_email": CONTACTOUT_CREDITS_PER_EMAIL,
    }


@app.get("/api/keys")
async def list_keys():
    return _keys_payload()


@app.post("/api/keys")
async def add_keys(req: KeysAddRequest):
    """Add ContactOut key(s) one at a time (or several pasted together)."""
    candidates = [k for k in re.split(r"[,;\s]+", req.keys or "") if k.strip()]
    if not candidates:
        raise HTTPException(status_code=400, detail="Paste an API key first.")
    added = sum(1 for k in candidates if CONTACTOUT_POOL.add_key(k))
    payload = _keys_payload()
    payload["added"] = added
    payload["skipped"] = len(candidates) - added      # duplicates or too short to be a key
    return payload


@app.post("/api/keys/test")
async def test_keys():
    """Ask ContactOut whether each key is accepted. Costs no credits (it looks up a profile that does not exist)."""
    keys = CONTACTOUT_POOL.keys
    results = await run_in_threadpool(lambda: [probe_contactout(k) for k in keys])
    out = []
    for k, r in zip(keys, results):
        if r["ok"]:
            CONTACTOUT_POOL.unblock(k)
        else:
            CONTACTOUT_POOL.note(k, r["message"])
        out.append({"id": _key_id(k), **r})
    payload = _keys_payload()
    payload["tests"] = out
    return payload


@app.delete("/api/keys/{key_id}")
async def remove_key(key_id: str):
    if not CONTACTOUT_POOL.remove(key_id):
        raise HTTPException(status_code=404, detail="Key not found, or it comes from .env (edit .env to remove it).")
    return _keys_payload()


@app.post("/api/keys/{key_id}/reset")
async def reset_key(key_id: str):
    """Mark a key's credits as fresh again (use after your plan renews)."""
    if not CONTACTOUT_POOL.reset(key_id):
        raise HTTPException(status_code=404, detail="Key not found.")
    return _keys_payload()


@app.post("/api/find-email")
async def find_email(req: EmailRequest):
    """Keyless email lookup for one recruiter (published email, learned company format, or a labelled guess)."""
    try:
        info = await run_in_threadpool(
            find_recruiter_email,
            full_name=req.name,
            company_name=req.company,
            linkedin_url=req.linkedin_url,
            snippet=req.snippet,
            use_paid=req.use_paid,
        )
        return {"success": True, **info}
    except Exception as e:
        return {"success": False, "message": str(e), "primary_email": "Not Found", "confidence": "None"}


@app.post("/api/sync-sheets")
async def sync_leads(req: SyncRequest):
    global sheets_manager
    if not req.leads:
        return {"added": 0, "duplicates": 0, "sheet_synced": False}

    res = await run_in_threadpool(sheets_manager.save_leads, req.leads, req.webhook_url, req.batch)
    return res


@app.post("/api/generate-email")
async def create_email_draft(req: EmailDraftRequest):
    draft = generate_email_draft(
        recruiter_name=req.recruiter_name,
        company=req.company,
        role_category=req.role_category,
        candidate_name=req.candidate_name or "Your Name",
    )
    return draft


@app.get("/api/download-csv")
async def download_csv():
    master_csv = DATA_DIR / "leads_master.csv"
    if not master_csv.exists():
        raise HTTPException(status_code=404, detail="No leads exported to CSV yet.")
    return FileResponse(
        path=master_csv,
        media_type="text/csv",
        filename="recruiter_leads.csv",
    )


def find_available_port(preferred_port: int = 8000) -> int:
    """Finds an available port starting from preferred_port with exclusive bind check on Windows."""
    import socket
    for port in [preferred_port, 8001, 8002, 8005, 8501, 5000, 3000]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    return preferred_port


if __name__ == "__main__":
    import uvicorn
    import sys

    port = 8000
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    else:
        port = find_available_port(8000)

    print(f"\n[*] Starting AutoRecruit Web Dashboard at http://127.0.0.1:{port} ...")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
