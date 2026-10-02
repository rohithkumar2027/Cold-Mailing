from ddgs import DDGS
import re

d = DDGS(timeout=10)

recruiter_tests = [
    ("Ritu Singh", "Google", "https://www.linkedin.com/in/ritusingh01"),
    ("Narendra Babu", "Google", "https://in.linkedin.com/in/nnarendrababu"),
    ("Akanksha Pandey", "Google", "https://in.linkedin.com/in/akanksha-pandey-5a61b785"),
    ("Kavya Shree", "Google", "https://in.linkedin.com/in/kavyashreeav"),
    ("Nikhil Joseph", "Google", "https://in.linkedin.com/in/nikhil-joseph-1115b716"),
    ("Vikas Kaushik", "Google", "https://in.linkedin.com/in/vikas-kaushik-2914b5135"),
]

for name, company, profile in recruiter_tests:
    slug = profile.rstrip('/').split('/')[-1]
    queries = [
        f'"{name}" "{company}" "@google.com"',
        f'site:linkedin.com "{name}" "email"',
        f'"{slug}" email',
    ]
    print(f"\n================= {name} =================")
    found_emails = set()
    for q in queries:
        try:
            results = d.text(q, backend="duckduckgo,yahoo,brave", max_results=3)
            for r in results:
                combined = f"{r.get('title', '')} {r.get('body', '')}"
                found = re.findall(r'[a-zA-Z0-9_.+-]+@google\.com', combined, re.IGNORECASE)
                for e in found:
                    found_emails.add(e.lower())
                found_gmail = re.findall(r'[a-zA-Z0-9_.+-]+@gmail\.com', combined, re.IGNORECASE)
                for g in found_gmail:
                    found_emails.add(g.lower())
        except Exception as e:
            pass
    print(f"Found emails for {name}: {found_emails}")
