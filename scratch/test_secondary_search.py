from ddgs import DDGS
import re

ddg = DDGS()

# Test searching for published email of a specific recruiter
recruiters = [
    ("Ritu Singh", "Google", "google.com"),
    ("Shuchi Garg", "Google", "google.com"),
    ("Akanksha Pandey", "Google", "google.com"),
    ("Jhoom Choudhary", "Google", "google.com"),
]

for name, comp, domain in recruiters:
    query = f'"{name}" "{comp}" ("@{domain}" OR "email")'
    print(f"\n--- Searching for real email of: {name} at {comp} ---")
    results = list(ddg.text(query, max_results=3))
    found_emails = []
    for r in results:
        text = f"{r.get('title', '')} {r.get('body', '')}"
        emails = re.findall(rf'[a-zA-Z0-9_.+-]+@{re.escape(domain)}', text, re.IGNORECASE)
        found_emails.extend(emails)
    
    unique_emails = list(set(found_emails))
    print(f"Results checked: {len(results)}, Found emails: {unique_emails}")
