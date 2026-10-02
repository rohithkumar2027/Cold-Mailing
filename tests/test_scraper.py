"""Offline tests for the recruiter scraper (no network: search is injected)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.scraper import (  # noqa: E402
    build_search_queries,
    clean_linkedin_url,
    company_aliases,
    evaluate_profile,
    find_recruiters_for_company,
    parse_linkedin_title,
)


def hit(name_title, snippet, slug):
    return {"href": f"https://in.linkedin.com/in/{slug}", "title": name_title, "body": snippet}


GOOD = [
    ("Priya Sharma - Technical Recruiter - Google | LinkedIn",
     "Experience: Google · Education: NIT · Location: Bengaluru · 500+ connections", "priya-sharma-1"),
    ("Rahul Verma - Talent Acquisition Partner at Google India | LinkedIn",
     "Location: Hyderabad, Telangana, India", "rahul-verma"),
    ("Anita Rao - Campus Recruiter (SDE/ML) - Google | LinkedIn",
     "Experience: Google · Location: Pune", "anita-rao"),
    ("Sneha Iyer - Google | LinkedIn",
     "Experience: Google · Location: Bengaluru · Sourcer for AI and machine learning hiring", "sneha-iyer"),
]
BAD = [
    ("Amit Jain - Ex-Google Recruiter - Microsoft | LinkedIn", "Experience: Microsoft · Location: Pune, India", "amit-jain"),
    ("Kiran Patel - Senior Recruiter - Deloitte | LinkedIn",
     "Formerly Technical Recruiter at Google. Experience: Deloitte · Location: Mumbai, India", "kiran-patel"),
    ("Neha Gupta - Software Engineer - Google | LinkedIn",
     "Experience: Google · we are hiring through referrals · Location: Bengaluru", "neha-gupta"),
    ("Vikram Singh - HR Business Partner - Google | LinkedIn", "Experience: Google · Location: Gurugram", "vikram-singh"),
    ("Meera Nair - Technical Recruiter at Metadata Labs | LinkedIn", "Experience: Metadata Labs · Location: Kochi, India", "meera-nair"),
    ("Rohan Das - Sales Recruiter - Google | LinkedIn", "Experience: Google · Location: Delhi, India", "rohan-das"),
]


def test_current_recruiters_accepted():
    for title, snip, slug in GOOD:
        v = evaluate_profile(title, snip, "Google")
        assert v["ok"], (slug, v)


def test_wrong_people_rejected():
    for title, snip, slug in BAD:
        company = "Meta" if "Metadata" in title else "Google"
        v = evaluate_profile(title, snip, company)
        assert not v["ok"], (slug, v)


def test_substring_false_positives_gone():
    # "hr" inside "through" and "meta" inside "metadata" must not count
    assert not evaluate_profile("Foo Bar - Engineer - Google | LinkedIn", "Experience: Google · through and through", "Google")["ok"]
    assert not evaluate_profile("Foo Bar - Recruiter - Metadata Inc | LinkedIn", "Experience: Metadata Inc", "Meta")["ok"]


def test_aliases():
    assert "facebook" in company_aliases("Meta")
    assert "tata consultancy services" in company_aliases("TCS")
    v = evaluate_profile("Asha K - Technical Recruiter - Facebook | LinkedIn", "Experience: Facebook · Location: Hyderabad, India", "Meta")
    assert v["ok"]


def test_query_volume_and_depth():
    deep = build_search_queries("Google", "ai_ml", "India", depth="deep")
    fast = build_search_queries("Google", "ai_ml", "India", depth="fast")
    assert len(deep) >= 35 and len(set(deep)) == len(deep)
    assert 8 <= len(fast) < len(deep)
    assert any("machine learning" in q for q in deep)


def test_url_cleaning_and_dedupe():
    assert clean_linkedin_url("https://in.linkedin.com/in/Priya-Sharma/?trk=x") == "https://www.linkedin.com/in/priya-sharma"


def test_find_merges_many_queries_and_keeps_same_named_people():
    # two DIFFERENT people with the same name must both survive (old code dropped one)
    data = {
        0: [hit(*GOOD[0]), hit(*GOOD[1]), hit(*BAD[0])],
        1: [hit(*GOOD[2]), hit(*GOOD[0]), hit(*BAD[1])],  # duplicate of GOOD[0]
        2: [hit(*GOOD[3]), hit(*BAD[2]), hit(*BAD[3]),
            hit("Priya Sharma - Talent Acquisition - Google | LinkedIn", "Experience: Google · Location: Pune, India", "priya-sharma-2")],
    }
    calls = {"n": 0}

    def fake_search(q):
        i = calls["n"] % 3
        calls["n"] += 1
        return data[i]

    leads = find_recruiters_for_company("Google", "swe", max_results=0, search_fn=fake_search, depth="fast")
    urls = {l["linkedin_url"] for l in leads}
    assert "https://www.linkedin.com/in/priya-sharma-1" in urls
    assert "https://www.linkedin.com/in/priya-sharma-2" in urls
    assert len(leads) == 5
    assert not any("amit-jain" in u or "kiran-patel" in u or "neha-gupta" in u for u in urls)
    assert all(l["employment_status"].startswith(("Verified", "Likely")) for l in leads)


def test_limit_and_ranking():
    def fake_search(q):
        return [hit(*g) for g in GOOD]
    leads = find_recruiters_for_company("Google", "swe", max_results=2, search_fn=fake_search, depth="fast")
    assert len(leads) == 2
    assert leads[0]["match_confidence"] == "Verified"


def test_parse_title():
    p = parse_linkedin_title("Priya Sharma - Technical Recruiter - Google | LinkedIn", "Google")
    assert p["name"] == "Priya Sharma" and p["title"] == "Technical Recruiter"


def test_real_world_wrong_matches_rejected():
    # current job elsewhere, ex-Walmart, even if a stale index lists Walmart first
    v = evaluate_profile(
        "Aishwarya D - Senior Early Careers Recruiter at Standard Chartered | LinkedIn",
        "Experience: Walmart · Senior Early Careers- APAC Recruiter at Standard Chartered Ex-Flipkart Ex-Walmart", "Walmart")
    assert not v["ok"], v
    v = evaluate_profile("Aishwarya D - Senior Early Careers Recruiter | LinkedIn",
                         "Experience: Walmart · Ex-Flipkart Ex-Walmart Ex-KhataBook", "Walmart")
    assert not v["ok"], v
    # snippet belongs to someone else / company is only a client
    v = evaluate_profile("Catherine Kasbe - Sr. Talent Acquisition Specialist | LinkedIn",
                         "View Mohit Bansal’s profile on LinkedIn. Recruiter at Walmart.", "Walmart")
    assert not v["ok"], v
    v = evaluate_profile("Some One - Sr. Talent Acquisition Specialist | LinkedIn",
                         "Partnered with clients such as Walmart, PepsiCo to address their recruitment needs.", "Walmart")
    assert not v["ok"], v
    # open to work
    v = evaluate_profile("Dhaval G - Talent Acquisition Lead - Walmart | LinkedIn",
                         "Recruiter open to work Immediate Joiner Experienced US IT Recruiter", "Walmart")
    assert not v["ok"], v
    # an earlier employer with 'Ex-' must not block a real current one
    v = evaluate_profile("Ghousiya S - Talent Acquisition Lead (South Asia)@Walmart | LinkedIn",
                         "Ex-HP Inc & ABB · Location: Bengaluru", "Walmart")
    assert v["ok"] and v["confidence"] == "Verified", v


def test_verified_only():
    def fake_search(q):
        return [hit(*GOOD[0]),
                hit("Ravi Kumar - Technical Recruiter | LinkedIn", "Technical recruiter at Google. Location: Pune, India", "ravi-kumar")]
    assert len(find_recruiters_for_company("Google", max_results=0, search_fn=fake_search, depth="fast")) == 2
    only = find_recruiters_for_company("Google", max_results=0, search_fn=fake_search, depth="fast", verified_only=True)
    assert [l["name"] for l in only] == ["Priya Sharma"]
