from ddgs import DDGS
import re

ddg = DDGS()

queries = [
    'site:in.linkedin.com/in ("Technical Recruiter" OR "University Recruiter" OR "Talent Acquisition") "Google" ("Bengaluru" OR "Bangalore" OR "Hyderabad" OR "India")',
    'site:linkedin.com/in ("Technical Recruiter" OR "University Recruiter") "Google" "India" ("Bengaluru" OR "Hyderabad" OR "Gurgaon")',
    'site:linkedin.com/in "Google" "recruiter" "India" ("@google.com" OR "mail" OR "email" OR "reach out")',
]

for q in queries:
    print(f"\n================ QUERY: {q} ================")
    results = list(ddg.text(q, max_results=5))
    print(f"Count: {len(results)}")
    for r in results:
        title = r.get("title", "")
        snippet = r.get("body", "")
        url = r.get("href", "")
        combined = f"{title} {snippet}"
        emails = re.findall(r'[a-zA-Z0-9_.+-]+@google\.com', combined, re.IGNORECASE)
        
        print("TITLE:", title)
        print("URL:", url)
        print("SNIPPET:", snippet[:160])
        print("DETECTED REAL EMAILS:", emails)
        print("-" * 40)
