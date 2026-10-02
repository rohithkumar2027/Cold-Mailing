"""
Google Sheets & CSV Manager Module
Handles syncing recruiter leads to Google Sheets via gspread and creates local CSV backups.
"""
import os
import re
import csv
import logging
from pathlib import Path
from typing import List, Dict, Optional
import requests

try:
    import gspread  # type: ignore
    from google.oauth2.service_account import Credentials  # type: ignore
except ImportError:
    gspread = None  # type: ignore
    Credentials = None  # type: ignore

from config.settings import (
    DEFAULT_SHEET_HEADERS,
    DEFAULT_GOOGLE_SHEET_NAME,
    GOOGLE_CREDENTIALS_FILE,
    GOOGLE_SHEET_WEBHOOK_URL,
    DATA_DIR,
)

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class SheetsManager:
    def __init__(
        self,
        sheet_name: str = DEFAULT_GOOGLE_SHEET_NAME,
        credentials_file: str = GOOGLE_CREDENTIALS_FILE,
    ):
        self.sheet_name = sheet_name
        self.credentials_file = Path(credentials_file)
        self.gc: Optional[gspread.Client] = None
        self.worksheet: Optional[gspread.Worksheet] = None
        self.is_connected = False
        self.connection_error = ""

        self._initialize_google_sheet()

    def _initialize_google_sheet(self):
        """Attempts to authenticate and connect to the Google Sheet."""
        if not self.credentials_file.exists():
            self.connection_error = (
                f"Google service account file not found at: {self.credentials_file}. "
                "Leads will be saved to local CSV."
            )
            logger.warning(self.connection_error)
            return

        try:
            creds = Credentials.from_service_account_file(
                str(self.credentials_file), scopes=SCOPES
            )
            self.gc = gspread.authorize(creds)

            # Try to open existing sheet
            try:
                spreadsheet = self.gc.open(self.sheet_name)
                self.worksheet = spreadsheet.sheet1
            except gspread.SpreadsheetNotFound:
                # Create a new spreadsheet if it doesn't exist
                spreadsheet = self.gc.create(self.sheet_name)
                self.worksheet = spreadsheet.sheet1
                # Write header row
                self.worksheet.append_row(DEFAULT_SHEET_HEADERS)

            # Check if headers exist, if sheet is empty write headers
            existing_values = self.worksheet.get_all_values()
            if not existing_values:
                self.worksheet.append_row(DEFAULT_SHEET_HEADERS)

            self.is_connected = True
            logger.info(f"Successfully connected to Google Sheet: {self.sheet_name}")

        except Exception as e:
            self.is_connected = False
            self.connection_error = str(e)
            logger.error(f"Failed to connect to Google Sheets: {e}")

    @staticmethod
    def _lead_key(name: str, company: str) -> str:
        return f"{str(name).strip().lower()}|{str(company).strip().lower()}"

    def get_existing_leads(self) -> set:
        """Keys (name|company) of recruiters already saved, to avoid duplicates."""
        seen = set()

        if self.is_connected and self.worksheet:
            try:
                for r in self.worksheet.get_all_records():
                    name, comp = r.get("Recruiter Name", ""), r.get("Company", "")
                    if name:
                        seen.add(self._lead_key(name, comp))
            except Exception as e:
                logger.error(f"Error fetching existing sheet records: {e}")

        master_csv = DATA_DIR / "leads_master.csv"
        if master_csv.exists():
            try:
                with open(master_csv, mode="r", encoding="utf-8-sig", errors="replace") as f:
                    for r in csv.DictReader(f):
                        name, comp = r.get("Recruiter Name", ""), r.get("Company", "")
                        if name:
                            seen.add(self._lead_key(name, comp))
            except Exception:
                pass
        return seen

    @staticmethod
    def _migrate_master_csv(path: Path) -> None:
        """If the master CSV uses an older column layout, keep it as a legacy file and start a fresh one."""
        try:
            with open(path, mode="r", newline="", encoding="utf-8-sig", errors="replace") as f:
                header = next(csv.reader(f), [])
            if header and [h.strip() for h in header] != list(DEFAULT_SHEET_HEADERS):
                from datetime import datetime
                legacy = path.with_name(f"leads_master_legacy_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
                path.rename(legacy)
        except Exception as e:  # never block saving because of a migration problem
            logger.warning(f"Could not migrate master CSV: {e}")

    def _upsert_master(self, path: Path, all_rows: Dict[str, list]) -> int:
        """Add new rows to the master CSV and fill in emails for rows that were saved without one.
        Returns how many existing rows got an email. Rewrites the file atomically."""
        rows: List[list] = []
        if path.exists():
            try:
                with open(path, mode="r", newline="", encoding="utf-8-sig", errors="replace") as f:
                    reader = csv.reader(f)
                    next(reader, None)  # header
                    rows = [r + [""] * (4 - len(r)) for r in reader if any(c.strip() for c in r)]
            except Exception as e:
                logger.warning(f"Could not read master CSV, appending only: {e}")
        index = {self._lead_key(r[0], r[2]): i for i, r in enumerate(rows)}
        updated = 0
        for key, new in all_rows.items():
            i = index.get(key)
            if i is None:
                index[key] = len(rows)
                rows.append(list(new))
                continue
            cur = rows[i]
            if new[1] and cur[1].strip().lower() != new[1].lower():
                if not cur[1].strip():
                    updated += 1
                cur[1] = new[1]              # a real email found later replaces a blank
            if new[3] and not cur[3].strip():
                cur[3] = new[3]
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, mode="w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(DEFAULT_SHEET_HEADERS)
            writer.writerows(rows)
        os.replace(tmp, path)
        return updated

    def save_leads(self, leads: List[Dict], webhook_url: Optional[str] = None, write_batch: bool = True) -> Dict:
        """
        Saves a list of leads to Google Sheets (if connected) and local CSV.
        Columns: A Recruiter Name, B Email, C Company, D About the Recruiter
        Returns summary dictionary.
        """
        if not leads:
            return {
                "added": 0,
                "duplicates": 0,
                "sheet_synced": False,
                "csv_path": "",
                "connection_error": self.connection_error,
            }

        seen = self.get_existing_leads()
        all_rows: Dict[str, list] = {}   # key -> row, every lead in this batch (used to update the master CSV)
        new_rows = []                    # rows never saved before (sent to Google Sheets / webhook)
        duplicates_count = 0

        for lead in leads:
            name = str(lead.get("name", "")).strip()
            company = str(lead.get("company", "")).strip()
            key = self._lead_key(name, company)
            if not name:
                duplicates_count += 1
                continue

            email = str(lead.get("primary_email", "") or "").strip()
            if "@" not in email:  # "Not Found" etc. -> leave blank; never store a guess
                email = ""
            about = str(lead.get("about", "") or "").strip() or str(lead.get("title", "") or "").strip()

            # A: Recruiter Name | B: Email | C: Company | D: About the Recruiter
            row = [name, email, company, about]
            if key in all_rows:              # same person twice in one batch: keep the copy that has an email
                duplicates_count += 1
                if email and not all_rows[key][1]:
                    all_rows[key][1] = email
                continue
            all_rows[key] = row
            if key in seen:
                duplicates_count += 1
            else:
                new_rows.append(row)

        # 1. Sync to Google Sheets (Service Account OR Webhook)
        sheet_synced = False
        webhook_url = webhook_url or GOOGLE_SHEET_WEBHOOK_URL
        if webhook_url and new_rows:
            try:
                resp = requests.post(webhook_url, json={"headers": DEFAULT_SHEET_HEADERS, "rows": new_rows}, timeout=10)
                if resp.status_code in [200, 201]:
                    sheet_synced = True
                    logger.info("Successfully synced leads to Google Sheet via Webhook!")
            except Exception as e:
                logger.error(f"Error posting to Google Sheet webhook: {e}")
                self.connection_error = f"Webhook error: {e}"

        if not sheet_synced and self.is_connected and self.worksheet and new_rows:
            try:
                self.worksheet.append_rows(new_rows)
                sheet_synced = True
            except Exception as e:
                logger.error(f"Error appending rows to Google Sheet: {e}")
                self.connection_error = str(e)

        # 2. Always update the local master CSV (UTF-8 BOM for Excel).
        #    New recruiters are appended; recruiters already there get their Email filled in
        #    when one has been found since they were first saved.
        master_csv_path = DATA_DIR / "leads_master.csv"
        if master_csv_path.exists():
            self._migrate_master_csv(master_csv_path)
        updated_count = self._upsert_master(master_csv_path, all_rows)

        # 3. Timestamped batch CSV (only when the user downloads)
        batch_csv_path = ""
        if write_batch:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            batch_csv_path = DATA_DIR / f"leads_batch_{timestamp}.csv"
            with open(batch_csv_path, mode="w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(DEFAULT_SHEET_HEADERS)
                for r in all_rows.values():
                    writer.writerow(r)

        return {
            "added": len(new_rows),
            "updated": updated_count,
            "duplicates": duplicates_count,
            "sheet_synced": sheet_synced,
            "sheet_url": self.worksheet.url if (self.is_connected and self.worksheet) else "",
            "csv_path": str(master_csv_path),
            "batch_csv": str(batch_csv_path) if batch_csv_path else "",
            "connection_error": self.connection_error,
        }
