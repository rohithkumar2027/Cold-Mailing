"""
Main CLI Pipeline for Recruiter Lead Generator & Cold Mailing Automation
Usage:
    python main.py
    python main.py --company "Google" --role "swe" --limit 25 --depth deep
    python main.py --companies "Google,Meta,Stripe" --role "ai_ml"
"""
import sys
import argparse
import time
from typing import List, Dict

# Fix Windows console UTF-8 encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


from modules.scraper import find_recruiters_for_company
from modules.email_finder import find_recruiter_email
from modules.sheets_manager import SheetsManager
from modules.email_templates import generate_email_draft
from config.settings import ROLE_KEYWORDS


def process_company_recruiters(
    company: str, role_category: str = "swe", limit: int = 25, location: str = "India",
    depth: str = "deep", tech_only: bool = False
) -> List[Dict]:
    """
    Finds recruiters for a given company in India and enriches them with real published emails.
    """
    print(f"\n🔍 Searching recruiters at '{company}' in {location} for {ROLE_KEYWORDS.get(role_category, {}).get('label', role_category)}...")
    raw_recruiters = find_recruiters_for_company(company, role_category=role_category, max_results=limit, location=location,
                                              depth=depth, tech_only=tech_only)

    if not raw_recruiters:
        print(f"⚠️  No public LinkedIn recruiter profiles found for '{company}' in {location}.")
        return []

    print(f"✅ Found {len(raw_recruiters)} {location} recruiters. Extracting real published emails & verifying...")

    enriched_leads = []
    for r in raw_recruiters:
        email_info = find_recruiter_email(
            full_name=r["name"],
            company_name=company,
            linkedin_url=r.get("linkedin_url", ""),
            snippet=r.get("snippet", ""),
        )
        lead = {
            **r,
            **email_info,
        }
        enriched_leads.append(lead)

    return enriched_leads


def display_leads_table(leads: List[Dict]):
    """Pretty prints recruiter leads to console."""
    if not leads:
        return

    print("\n" + "=" * 135)
    print(f"{'NAME':<20} | {'EMAIL':<30} | {'PHONE':<13} | {'COMPANY':<10} | {'LOC':<7} | {'STATUS':<24} | {'CONFIDENCE':<16}")
    print("-" * 135)
    for lead in leads:
        name = lead.get("name", "")[:19]
        email = lead.get("primary_email", "")[:29]
        phone = lead.get("phone_number", "") or "N/A"
        phone = phone[:12]
        company = lead.get("company", "")[:9]
        loc = lead.get("location", "India")[:6]
        status = lead.get("employment_status", "Active")[:23]
        confidence = lead.get("confidence", "")[:15]
        print(f"{name:<20} | {email:<30} | {phone:<13} | {company:<10} | {loc:<7} | {status:<24} | {confidence:<16}")
    print("=" * 135 + "\n")


def run_interactive():
    """Interactive CLI menu for the user."""
    print("\n" + "🚀 " + "=" * 60)
    print("      RECRUITER LEAD GENERATOR & COLD MAILING AUTOMATION")
    print("=" * 64 + "\n")

    company_input = input("🏢 Enter company name(s) (comma-separated, e.g. Google, Meta, Stripe): ").strip()
    if not company_input:
        print("❌ Company name is required. Exiting.")
        return

    companies = [c.strip() for c in company_input.split(",") if c.strip()]

    print("\n🎯 Select Target Role Category:")
    print("  1. Software Engineering (SWE)")
    print("  2. AI / Machine Learning (AI/ML)")
    print("  3. Data Science & Analytics")
    choice = input("Enter choice (1-3) [default: 1]: ").strip()

    role_map = {"1": "swe", "2": "ai_ml", "3": "data_science"}
    role_category = role_map.get(choice, "swe")

    limit_str = input("\n📊 Number of recruiters per company (0 = all found) [default: 25]: ").strip()
    try:
        limit = int(limit_str) if limit_str else 25
    except ValueError:
        limit = 25

    all_leads = []
    for company in companies:
        leads = process_company_recruiters(company, role_category=role_category, limit=limit)
        all_leads.extend(leads)
        time.sleep(1)

    display_leads_table(all_leads)

    if not all_leads:
        print("No leads gathered.")
        return

    # Sync to Google Sheets & Local CSV
    print("💾 Saving leads to Google Sheet & Local CSV...")
    manager = SheetsManager()
    result = manager.save_leads(all_leads)

    print(f"\n📊 Summary:")
    print(f"  • New Leads Saved : {result['added']}")
    print(f"  • Duplicates Skipped: {result['duplicates']}")
    if result["sheet_synced"]:
        print(f"  • Google Sheet Sync : SUCCESS ({result.get('sheet_url', 'Updated')})")
    else:
        print(f"  • Google Sheet Sync : NOT CONNECTED ({result.get('connection_error', 'Missing credentials.json')})")
    print(f"  • Local Master CSV  : {result['csv_path']}")
    print(f"  • Batch CSV Backup  : {result['batch_csv']}")

    # Preview Cold Email Draft
    preview_email = input("\n✉️  Would you like to preview a personalized cold email draft? (y/n) [default: y]: ").strip().lower()
    if preview_email != "n":
        candidate_name = input("Your Full Name [default: Your Name]: ").strip() or "Your Name"
        lead = all_leads[0]
        draft = generate_email_draft(
            recruiter_name=lead["name"],
            company=lead["company"],
            role_category=role_category,
            candidate_name=candidate_name,
        )
        print("\n" + "-" * 60)
        print(f"TO: {lead['primary_email']}")
        print(f"SUBJECT: {draft['subject']}")
        print("-" * 60)
        print(draft["body"])
        print("-" * 60)


def main():
    parser = argparse.ArgumentParser(description="Automate Recruiter Leads & Cold Mailing")
    parser.add_argument("--company", type=str, help="Single target company (e.g. 'Google')")
    parser.add_argument("--companies", type=str, help="Comma-separated list of companies (e.g. 'Google,Meta,Stripe')")
    parser.add_argument(
        "--role",
        type=str,
        choices=["swe", "ai_ml", "data_science"],
        default="swe",
        help="Target role category (swe, ai_ml, data_science)",
    )
    parser.add_argument("--limit", type=int, default=25, help="Max recruiters per company (0 = everything found)")
    parser.add_argument("--depth", choices=["fast", "deep"], default="deep", help="deep runs ~40 search variants (finds more people)")
    parser.add_argument("--tech-only", action="store_true", help="Only keep recruiters with a tech/campus/AI-ML focus")
    parser.add_argument("--location", type=str, default="India", help="Target location (default: 'India')")

    args = parser.parse_args()

    if not args.company and not args.companies:
        run_interactive()
        return

    companies = []
    if args.company:
        companies.append(args.company.strip())
    if args.companies:
        companies.extend([c.strip() for c in args.companies.split(",") if c.strip()])

    all_leads = []
    for comp in companies:
        leads = process_company_recruiters(comp, role_category=args.role, limit=args.limit, location=args.location,
                                          depth=args.depth, tech_only=args.tech_only)
        all_leads.extend(leads)

    display_leads_table(all_leads)

    manager = SheetsManager()
    result = manager.save_leads(all_leads)
    print(f"Result: {result['added']} added, {result['duplicates']} skipped.")
    print(f"Master CSV: {result['csv_path']}")
    if result["sheet_synced"]:
        print("Google Sheet updated successfully!")
    else:
        print(f"Google Sheet note: {result['connection_error']}")


if __name__ == "__main__":
    main()
