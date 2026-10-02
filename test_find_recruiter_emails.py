from ddgs import DDGS
import re

d = DDGS(timeout=10)

queries = [
    'site:in.linkedin.com/in "Google" "Recruiter" "@google.com"',
    'site:in.linkedin.com/in "Google" "Technical Recruiter" "@google.com"',
    'site:in.linkedin.com/in "Google" "Recruiter" "@gmail.com"',
    'site:in.linkedin.com/in "Google" "recruiter" "email"',
    'site:in.linkedin.com/in "Google" "recruiting" "reach me at"',
    'site:in.linkedin.com/in "Google" "talent" "email me"',
    'site:linkedin.com "recruiter at Google" "India" "@google.com"',
    'site:linkedin.com/posts "Google" recruiter "India" "@google.com"',
    'site:linkedin.com/posts "Google" "technical recruiter" "@google.com"',
]

for q in queries:
    print(f"\n=== Query: {q} ===")
    try:
        res = d.text(q, backend="duckduckgo,yahoo,brave", max_results=5)
        for r in res:
            title = r.get('title', '')
            body = r.get('body', '')
            url = r.get('href', '')
            text = f"{title} {body}"
            # Extract emails
            emails = re.findall(r'[a-zA-Z0-9_.+-]+@(?:google\.com|gmail\.com)', text, re.IGNORECASE)
            if emails:
                print(f"MATCH: {title[:60]} | URL: {url} | EMAILS: {emails}")
            else:
                print(f"No email in snippet: {title[:50]} | URL: {url}")
    except Exception as e:
        print(f"Error: {e}")
