from ddgs import DDGS
import re

ddg = DDGS()

def extract_real_email_for_recruiter(name: str, company: str, domain: str = "google.com", initial_text: str = ""):
    # 1. Check initial text (from snippet & title)
    regex = rf'[a-zA-Z0-9_.+-]+@{re.escape(domain)}'
    emails = re.findall(regex, initial_text, re.IGNORECASE)
    if emails:
        # filter out generic emails like feedback@google.com or support@google.com
        filtered = [e for e in emails if not any(g in e.lower() for g in ["support", "help", "feedback", "press", "legal", "security", "jobs", "careers"])]
        if filtered:
            return filtered[0], "Verified (Published on LinkedIn)"

    # 2. Secondary targeted search for this specific person
    query = f'"{name}" "{company}" ("@{domain}" OR "email")'
    try:
        results = list(ddg.text(query, max_results=3))
        for r in results:
            text = f"{r.get('title', '')} {r.get('body', '')}"
            found = re.findall(regex, text, re.IGNORECASE)
            filtered = [e for e in found if not any(g in e.lower() for g in ["support", "help", "feedback", "press", "legal", "security", "jobs", "careers"])]
            if filtered:
                return filtered[0], "Verified (Public Web / Profile)"
    except Exception:
        pass

    # 3. Fallback to known corporate pattern
    parts = name.split()
    first = parts[0].lower()
    last = parts[-1].lower() if len(parts) > 1 else ""
    if last:
        return f"{first}.{last}@{domain}", "Corporate Pattern (google.com)"
    else:
        return f"{first}@{domain}", "Corporate Pattern (google.com)"

test_candidates = [
    ("Ritu Singh", "Google Cloud Recruiter", "Please feel free to reach out to me at ritus@google.com to learn more."),
    ("Siddharth Pandey", "Talent Acquisition at Google", "An ambitious and results-oriented TA Recruiter at Google India"),
    ("Narendra Babu", "Technical Recruiter at Google", "Technical Recruiter at Google Bengaluru India"),
    ("Shankar Selvaraj", "Senior Recruiter @ Google", "Senior Recruiter @ Google Specialist in hiring Senior Engineering Leaders India"),
]

for name, title, snip in test_candidates:
    email, conf = extract_real_email_for_recruiter(name, "Google", "google.com", f"{title} {snip}")
    print(f"Candidate: {name} | Email: {email} | Confidence: {conf}")
