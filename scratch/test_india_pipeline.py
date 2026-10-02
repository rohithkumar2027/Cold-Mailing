from ddgs import DDGS
import re

INDIA_LOCATIONS = [
    "india", "bengaluru", "bangalore", "hyderabad", "gurgaon", "gurugram", 
    "mumbai", "pune", "delhi", "noida", "chennai", "karnataka", "telangana"
]

def is_in_india(url: str, snippet: str, title: str) -> bool:
    if "in.linkedin.com" in url.lower():
        return True
    combined = f"{title} {snippet}".lower()
    return any(loc in combined for loc in INDIA_LOCATIONS)

ddg = DDGS()
query = 'site:linkedin.com/in ("Technical Recruiter at Google" OR "Recruiter at Google" OR "University Recruiter at Google") ("India" OR "Bengaluru" OR "Hyderabad" OR "Gurgaon") -ex-Google'

print(f"Query: {query}")
results = list(ddg.text(query, max_results=10))

print(f"Results fetched: {len(results)}")
india_recruiters = []
for r in results:
    url = r.get("href", "")
    title = r.get("title", "")
    snippet = r.get("body", "")
    
    if not is_in_india(url, snippet, title):
        continue
        
    print(f"MATCH: {title}")
    print(f"  URL: {url}")
    print(f"  SNIPPET: {snippet[:120]}")
    india_recruiters.append(r)

print(f"\nTotal India recruiters found: {len(india_recruiters)}")
