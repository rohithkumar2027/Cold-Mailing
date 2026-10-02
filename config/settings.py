"""
Configuration and settings for Recruiter Lead Generator & Cold Mailing
"""
import os
import re
from pathlib import Path
from dotenv import load_dotenv

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"

# Ensure data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Load environment variables
load_dotenv(BASE_DIR / ".env")

# Google Sheets Configuration
DEFAULT_GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Recruiter_Leads_Internships")
GOOGLE_CREDENTIALS_FILE = os.getenv(
    "GOOGLE_CREDENTIALS_FILE", str(CONFIG_DIR / "credentials.json")
)
GOOGLE_SHEET_WEBHOOK_URL = os.getenv("GOOGLE_SHEET_WEBHOOK_URL", "")

# Optional API Keys for email & phone enrichment (ContactOut, Apollo, Hunter)
def parse_keys(*raw_values):
    """Turns 'k1,k2  k3;k4' (from .env) into a de-duplicated list, keeping order."""
    keys = []
    for raw in raw_values:
        for k in re.split(r"[,;\s]+", raw or ""):
            k = k.strip().strip("\"'")
            if k and k not in keys:
                keys.append(k)
    return keys


CONTACTOUT_API_KEY = os.getenv("CONTACTOUT_API_KEY", "")
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "")
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")

# Several keys can be supplied, separated by commas: CONTACTOUT_API_KEYS=key1,key2,key3
# When one key runs out of credits (or is rate-limited) the next one is used automatically.
# Credit accounting for ContactOut (free plan: 100 credits per key; one email lookup = 10 credits).
CONTACTOUT_CREDITS_PER_KEY = int(os.getenv("CONTACTOUT_CREDITS_PER_KEY", "100"))
CONTACTOUT_CREDITS_PER_EMAIL = int(os.getenv("CONTACTOUT_CREDITS_PER_EMAIL", "10"))
# Keys you add inside the app are kept in this local file (with how many credits each has used).
API_KEY_STORE = CONFIG_DIR / "api_keys.json"

CONTACTOUT_API_KEYS = parse_keys(os.getenv("CONTACTOUT_API_KEYS", ""), CONTACTOUT_API_KEY)
APOLLO_API_KEYS = parse_keys(os.getenv("APOLLO_API_KEYS", ""), APOLLO_API_KEY)

# Standard Google Sheet Column Headers
DEFAULT_SHEET_HEADERS = [
    "Recruiter Name",      # column A
    "Email",               # column B (only a real, published email - blank if none found)
    "Company",             # column C
    "About the Recruiter", # column D
]

# Target Role Categories & Keyword Mappings
ROLE_KEYWORDS = {
    "swe": {
        "label": "Software Engineering (SWE)",
        "titles": [
            "technical recruiter",
            "swe recruiter",
            "engineering recruiter",
            "university recruiter",
            "campus recruiter",
            "early talent recruiter",
            "talent acquisition software",
        ],
        "keywords": ["swe", "software engineer", "software engineering", "developer", "backend", "full stack"],
    },
    "ai_ml": {
        "label": "AI / Machine Learning",
        "titles": [
            "technical recruiter",
            "ai recruiter",
            "ml recruiter",
            "machine learning recruiter",
            "research recruiter",
            "university recruiter",
            "talent acquisition ai",
        ],
        "keywords": ["ai", "machine learning", "deep learning", "nlp", "computer vision", "llm", "genai", "data science"],
    },
    "data_science": {
        "label": "Data Science & Analytics",
        "titles": [
            "data science recruiter",
            "technical recruiter",
            "analytics recruiter",
            "university recruiter",
            "talent acquisition data",
        ],
        "keywords": ["data science", "data scientist", "machine learning", "analytics", "data analytics"],
    },
}

# Known Top Tech Company Domain Patterns
# Default pattern mapping: {company_key: {"domain": "...", "pattern": "first.last" | "flast" | "first" | etc.}}
KNOWN_COMPANY_PATTERNS = {
    "google": {"domain": "google.com", "pattern": "first.last"},
    "meta": {"domain": "meta.com", "pattern": "flast"},
    "facebook": {"domain": "meta.com", "pattern": "flast"},
    "amazon": {"domain": "amazon.com", "pattern": "flast"},
    "microsoft": {"domain": "microsoft.com", "pattern": "first.last"},
    "apple": {"domain": "apple.com", "pattern": "first.last"},
    "netflix": {"domain": "netflix.com", "pattern": "first.last"},
    "nvidia": {"domain": "nvidia.com", "pattern": "first.last"},
    "uber": {"domain": "uber.com", "pattern": "flast"},
    "airbnb": {"domain": "airbnb.com", "pattern": "first.last"},
    "stripe": {"domain": "stripe.com", "pattern": "first"},
    "openai": {"domain": "openai.com", "pattern": "first"},
    "palantir": {"domain": "palantir.com", "pattern": "flast"},
    "databricks": {"domain": "databricks.com", "pattern": "first.last"},
    "snowflake": {"domain": "snowflake.com", "pattern": "first.last"},
    "salesforce": {"domain": "salesforce.com", "pattern": "flast"},
    "oracle": {"domain": "oracle.com", "pattern": "first.last"},
    "adobe": {"domain": "adobe.com", "pattern": "flast"},
    "intel": {"domain": "intel.com", "pattern": "first.last"},
    "cisco": {"domain": "cisco.com", "pattern": "flast"},
    "linkedin": {"domain": "linkedin.com", "pattern": "flast"},
    "spotify": {"domain": "spotify.com", "pattern": "first.last"},
    "robinhood": {"domain": "robinhood.com", "pattern": "first.last"},
    "pinterest": {"domain": "pinterest.com", "pattern": "first.last"},
    "lyft": {"domain": "lyft.com", "pattern": "first.last"},
    "coinbase": {"domain": "coinbase.com", "pattern": "first.last"},
    "twosigma": {"domain": "twosigma.com", "pattern": "first.last"},
    "jane street": {"domain": "janestreet.com", "pattern": "flast"},
    "citadel": {"domain": "citadel.com", "pattern": "first.last"},
    "bloomberg": {"domain": "bloomberg.net", "pattern": "flast"},
    "tiktok": {"domain": "bytedance.com", "pattern": "first.last"},
    "bytedance": {"domain": "bytedance.com", "pattern": "first.last"},
    "tesla": {"domain": "tesla.com", "pattern": "flast"},
}


