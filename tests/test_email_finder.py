"""Offline tests: emails must be REAL published ones only; sheet has 4 columns (A-D)."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import modules.email_finder as ef  # noqa: E402
from modules.scraper import build_about  # noqa: E402


def offline(monkeypatch, results=None, pages=None):
    ef._DOMAIN_CACHE.clear(); ef._PATTERN_CACHE.clear()
    monkeypatch.setattr(ef, "_mx_cached", lambda d: True)
    monkeypatch.setattr(ef, "_search", lambda q, max_results=8: list(results or []))
    monkeypatch.setattr(ef, "_fetch_page_text", lambda url, timeout=5.0: (pages or {}).get(url, ""))


def test_never_guesses_even_for_known_companies(monkeypatch):
    offline(monkeypatch)
    for company in ("Google", "Meta", "Stripe", "Zorblax"):
        r = ef.find_recruiter_email("Priya Sharma", company)
        assert r["primary_email"] == "Not Found", company
        assert r["confidence"] == "None"


def test_own_published_email_is_returned(monkeypatch):
    offline(monkeypatch)
    r = ef.find_recruiter_email("Priya Sharma", "Google", snippet="Reach me: priya.sharma@google.com")
    assert r["primary_email"] == "priya.sharma@google.com"
    assert r["confidence"].startswith("Published")


def test_someone_elses_email_is_not_attributed(monkeypatch):
    offline(monkeypatch)
    snip = "Contact the team: rahul.verma@google.com or recruiting-india@google.com or arao@google.com"
    r = ef.find_recruiter_email("Priya Sharma", "Google", snippet=snip)
    assert r["primary_email"] == "Not Found"


def test_public_page_with_matching_company_email(monkeypatch):
    hits = [{"href": "https://example.org/team", "title": "Team", "body": ""}]
    pages = {"https://example.org/team": "Our recruiter Priya Sharma - priya.sharma@google.com. Also Rahul Verma rverma@google.com"}
    offline(monkeypatch, hits, pages)
    r = ef.find_recruiter_email("Priya Sharma", "Google")
    assert r["primary_email"] == "priya.sharma@google.com" and r["confidence"].startswith("Published")
    r2 = ef.find_recruiter_email("Rahul Verma", "Google")
    assert r2["primary_email"] == "rverma@google.com"


def test_lookalike_domain_and_personal_addresses_rejected(monkeypatch):
    hits = [{"href": "https://example.org/p", "title": "", "body": ""}]
    pages = {"https://example.org/p": "Priya Sharma priya.sharma@notgoogle.com and priya.sharma@gmail.com"}
    offline(monkeypatch, hits, pages)
    assert ef.find_recruiter_email("Priya Sharma", "Google")["primary_email"] == "Not Found"


def test_page_that_does_not_mention_the_person_is_ignored(monkeypatch):
    hits = [{"href": "https://example.org/p", "title": "", "body": ""}]
    pages = {"https://example.org/p": "Contact priya.sharma@google.com for support"}
    offline(monkeypatch, hits, pages)
    # page never names "Sharma"/"Priya" as a person block? it contains the local part only -> name words absent
    assert ef.find_recruiter_email("Anita Rao", "Google")["primary_email"] == "Not Found"


def test_about_uses_only_facts_from_the_result():
    about = build_about("Technical Recruiter", "Experience: Google · Infosys · Education: NIT Trichy · Location: Bengaluru · 500+ connections", "Google")
    assert about.startswith("Technical Recruiter at Google.")
    assert "Bengaluru" in about and "Infosys" in about and "NIT Trichy" in about
    bare = build_about("Recruiter", "", "Google")
    assert bare == "Recruiter at Google."


def test_sheet_has_four_columns_and_blank_email(tmp_path, monkeypatch):
    import modules.sheets_manager as sm
    monkeypatch.setattr(sm, "DATA_DIR", tmp_path)
    (tmp_path / "leads_master.csv").write_text(
        "Recruiter Name,Role,Company,LinkedIn Link,Email,Email Confidence\nOld Person,Rec,Google,https://x,,\n", encoding="utf-8-sig")
    m = sm.SheetsManager.__new__(sm.SheetsManager)
    m.sheet_name = "x"; m.is_connected = False; m.worksheet = None; m.connection_error = ""
    leads = [
        {"name": "Priya Sharma", "title": "Technical Recruiter", "company": "Google", "about": "Technical Recruiter at Google. Based in Pune.",
         "primary_email": "priya.sharma@google.com", "confidence": "Published (LinkedIn)"},
        {"name": "Rahul Verma", "title": "Talent Partner", "company": "Google", "about": "Talent Partner at Google.", "primary_email": "Not Found"},
    ]
    res = m.save_leads(leads)
    assert res["added"] == 2
    rows = list(csv.reader(open(tmp_path / "leads_master.csv", encoding="utf-8-sig")))
    assert rows[0] == ["Recruiter Name", "Email", "Company", "About the Recruiter"]
    assert rows[1] == ["Priya Sharma", "priya.sharma@google.com", "Google", "Technical Recruiter at Google. Based in Pune."]
    assert rows[2][1] == ""                                  # no email found -> blank, not a guess
    assert list(tmp_path.glob("leads_master_legacy_*.csv"))  # old-layout file kept, not lost
    assert m.save_leads(leads)["added"] == 0                 # duplicates skipped by name+company


# ---------------------------------------------------------------------------
# Multiple API keys: use the next key when one runs out
# ---------------------------------------------------------------------------
class FakeResp:
    def __init__(self, code, body=None):
        self.status_code = code
        self._body = body or {}
        self.text = ""

    def json(self):
        return self._body


def _pool(monkeypatch, keys):
    ef._API_CACHE.clear()
    pool = ef.KeyPool(keys, "ContactOut")
    monkeypatch.setattr(ef, "CONTACTOUT_POOL", pool)
    return pool


GOOD = {"status_code": 200, "profile": {"work_email": ["priya.sharma@google.com"], "email": ["priya.sharma@google.com", "p@gmail.com"]}}


def test_second_key_used_when_first_is_out_of_credits(monkeypatch):
    pool = _pool(monkeypatch, ["key_one", "key_two", "key_three"])
    used = []

    def fake_get(url, params=None, headers=None, timeout=None):
        used.append(headers["token"])
        return FakeResp(403) if headers["token"] == "key_one" else FakeResp(200, GOOD)

    monkeypatch.setattr(ef.requests, "get", fake_get)
    r = ef.enrich_with_contactout("https://www.linkedin.com/in/priya")
    assert r["primary_email"] == "priya.sharma@google.com" and r["confidence"].startswith("Verified")
    assert used == ["key_one", "key_two"]
    st = pool.status()
    assert (st["total"], st["active"]) == (3, 2)
    # next lookup goes straight to key_two - the used-up key is not retried
    used.clear(); ef._API_CACHE.clear()
    ef.enrich_with_contactout("https://www.linkedin.com/in/other")
    assert used == ["key_two"]


def test_rate_limited_key_is_skipped_then_all_exhausted(monkeypatch):
    pool = _pool(monkeypatch, ["a", "b"])
    monkeypatch.setattr(ef.requests, "get", lambda *a, **k: FakeResp(429))
    assert ef.enrich_with_contactout("https://www.linkedin.com/in/x") is None
    assert pool.status()["active"] == 0        # both set aside; no crash, no guess


def test_not_found_does_not_burn_other_keys(monkeypatch):
    pool = _pool(monkeypatch, ["a", "b"])
    calls = []
    monkeypatch.setattr(ef.requests, "get", lambda url, params=None, headers=None, timeout=None: (calls.append(1), FakeResp(404, {"profile": []}))[1])
    assert ef.enrich_with_contactout("https://www.linkedin.com/in/nobody") is None
    assert len(calls) == 1 and pool.status()["active"] == 2


def test_status_code_inside_body_also_rotates(monkeypatch):
    _pool(monkeypatch, ["a", "b"])
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResp(200, {"status_code": 402}) if headers["token"] == "a" else FakeResp(200, GOOD)
    monkeypatch.setattr(ef.requests, "get", fake_get)
    assert ef.enrich_with_contactout("https://www.linkedin.com/in/p")["primary_email"] == "priya.sharma@google.com"


def test_full_flow_published_first_then_contactout(monkeypatch):
    offline(monkeypatch)
    _pool(monkeypatch, ["a"])
    monkeypatch.setattr(ef.requests, "get", lambda *a, **k: FakeResp(200, GOOD))
    r = ef.find_recruiter_email("Priya Sharma", "Google", linkedin_url="https://www.linkedin.com/in/priya")
    assert r["primary_email"] == "priya.sharma@google.com" and "ContactOut" in r["confidence"]
    # no keys and nothing published -> still never a guess
    _pool(monkeypatch, [])
    ef._API_CACHE.clear()
    assert ef.find_recruiter_email("Priya Sharma", "Google", linkedin_url="https://www.linkedin.com/in/priya")["primary_email"] == "Not Found"


def test_apollo_locked_placeholder_is_rejected(monkeypatch):
    ef._API_CACHE.clear()
    pool = ef.KeyPool(["k"], "Apollo"); monkeypatch.setattr(ef, "APOLLO_POOL", pool)
    monkeypatch.setattr(ef.requests, "post", lambda *a, **k: FakeResp(200, {"person": {"email": "email_not_unlocked@google.com"}}))
    assert ef.enrich_with_apollo("Priya Sharma", "Google") is None


def test_parse_keys_from_env_string():
    assert ef.parse_keys("k1, k2;k3\nk1", " k4 ") == ["k1", "k2", "k3", "k4"]


# ---------------------------------------------------------------------------
# Credits: 100 per key, 10 per email  ->  5 keys = 50 emails, used key by key
# ---------------------------------------------------------------------------
def _credit_pool(monkeypatch, n=5, tmp=None):
    ef._API_CACHE.clear()
    store = (tmp / "keys.json") if tmp else None
    pool = ef.KeyPool([f"key_number_{i}" for i in range(1, n + 1)], "ContactOut", credits_per_key=100, cost_per_use=10, store=store)
    monkeypatch.setattr(ef, "CONTACTOUT_POOL", pool)
    return pool


def test_five_keys_give_exactly_fifty_emails_used_in_order(monkeypatch):
    pool = _credit_pool(monkeypatch)
    used = []

    def fake_get(url, params=None, headers=None, timeout=None):
        used.append(headers["token"])
        return FakeResp(200, GOOD)

    monkeypatch.setattr(ef.requests, "get", fake_get)
    results = [ef.enrich_with_contactout(f"https://www.linkedin.com/in/person-{i}") for i in range(55)]
    assert sum(1 for r in results if r) == 50            # 5 keys x 10 emails
    assert results[50:] == [None] * 5                     # 51st onwards: nothing left, no request sent
    assert len(used) == 50
    assert used[:10] == ["key_number_1"] * 10 and used[10:20] == ["key_number_2"] * 10   # a key is fully used first
    assert pool.status()["credits_left"] == 0 and pool.status()["emails_left"] == 0


def test_lookup_that_finds_nothing_costs_no_credits(monkeypatch):
    pool = _credit_pool(monkeypatch, n=1)
    monkeypatch.setattr(ef.requests, "get", lambda *a, **k: FakeResp(404, {"profile": []}))
    assert ef.enrich_with_contactout("https://www.linkedin.com/in/nobody") is None
    assert pool.status()["credits_left"] == 100


def test_parallel_lookups_never_overspend(monkeypatch):
    import threading
    pool = _credit_pool(monkeypatch)
    calls = []
    lock = threading.Lock()

    def fake_get(url, params=None, headers=None, timeout=None):
        with lock:
            calls.append(headers["token"])
        return FakeResp(200, GOOD)

    monkeypatch.setattr(ef.requests, "get", fake_get)
    threads = [threading.Thread(target=ef.enrich_with_contactout, args=(f"https://www.linkedin.com/in/p{i}",)) for i in range(120)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert len(calls) == 50
    assert all(calls.count(f"key_number_{i}") == 10 for i in range(1, 6))


def test_service_saying_out_of_credits_marks_key_used_up(monkeypatch):
    pool = _credit_pool(monkeypatch, n=2)
    monkeypatch.setattr(ef.requests, "get", lambda url, params=None, headers=None, timeout=None:
                        FakeResp(402) if headers["token"] == "key_number_1" else FakeResp(200, GOOD))
    assert ef.enrich_with_contactout("https://www.linkedin.com/in/a")["primary_email"]
    rows = {r["masked"]: r for r in pool.snapshot()}
    assert rows["••••er_1"]["remaining"] == 0 and rows["••••er_2"]["remaining"] == 90


def test_credit_usage_survives_restart_and_reset(monkeypatch, tmp_path):
    pool = _credit_pool(monkeypatch, n=1, tmp=tmp_path)
    monkeypatch.setattr(ef.requests, "get", lambda *a, **k: FakeResp(200, GOOD))
    ef.enrich_with_contactout("https://www.linkedin.com/in/a")
    again = ef.KeyPool(["key_number_1"], "ContactOut", credits_per_key=100, cost_per_use=10, store=tmp_path / "keys.json")
    assert again.status()["credits_left"] == 90
    again.reset(ef.key_id("key_number_1"))
    assert again.status()["credits_left"] == 100


def test_add_keys_one_by_one_in_the_app(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import app as app_module
    pool = ef.KeyPool([], "ContactOut", credits_per_key=100, cost_per_use=10, store=tmp_path / "keys.json")
    monkeypatch.setattr(app_module, "CONTACTOUT_POOL", pool)
    c = TestClient(app_module.app)

    r = c.post("/api/keys", json={"keys": "first_secret_key_1111"}).json()
    assert r["added"] == 1 and r["summary"]["credits_left"] == 100
    r = c.post("/api/keys", json={"keys": "second_secret_key_2222"}).json()
    assert r["summary"]["total"] == 2 and r["summary"]["emails_left"] == 20
    assert c.post("/api/keys", json={"keys": "first_secret_key_1111"}).json()["added"] == 0      # duplicate
    body = c.get("/api/keys").text
    assert "first_secret_key_1111" not in body and "second_secret_key_2222" not in body        # never sent back
    assert "••••1111" in body
    kid = c.get("/api/keys").json()["keys"][0]["id"]
    assert c.post(f"/api/keys/{kid}/reset").status_code == 200
    assert c.delete(f"/api/keys/{kid}").json()["summary"]["total"] == 1
    assert c.delete("/api/keys/deadbeef").status_code == 404
    # several pasted together also work
    assert c.post("/api/keys", json={"keys": "third_secret_key_3333, fourth_secret_key_4444"}).json()["added"] == 2


def test_email_found_later_fills_existing_row_and_preview_matches(tmp_path, monkeypatch):
    """Find emails -> save: a recruiter saved earlier without an email gets it in column B."""
    import modules.sheets_manager as sm
    monkeypatch.setattr(sm, "DATA_DIR", tmp_path)
    m = sm.SheetsManager.__new__(sm.SheetsManager)
    m.sheet_name = "x"; m.is_connected = False; m.worksheet = None; m.connection_error = ""
    lead = {"name": "Priya Sharma", "title": "Recruiter", "company": "Google", "about": "Recruiter at Google.", "primary_email": ""}
    other = {"name": "Rahul Verma", "title": "Talent Partner", "company": "Google", "about": "Talent Partner at Google.", "primary_email": "Not Found"}
    assert m.save_leads([lead, other])["added"] == 2
    lead["primary_email"] = "priya.sharma@google.com"                 # found by "Find emails"
    res = m.save_leads([lead, other], write_batch=False)
    assert res["added"] == 0 and res["updated"] == 1 and res["batch_csv"] == ""
    rows = list(csv.reader(open(tmp_path / "leads_master.csv", encoding="utf-8-sig")))
    assert rows[1] == ["Priya Sharma", "priya.sharma@google.com", "Google", "Recruiter at Google."]
    assert rows[2][1] == "" and len(rows) == 3                          # no duplicate rows
    assert len(list(tmp_path.glob("leads_batch_*.csv"))) == 1          # auto-save wrote no extra batch file
    res = m.save_leads([lead, other], write_batch=False)               # repeat = no change
    assert res["updated"] == 0 and len(list(csv.reader(open(tmp_path / "leads_master.csv", encoding="utf-8-sig")))) == 3


def test_403_is_not_counted_as_out_of_credits_and_reason_is_reported(tmp_path, monkeypatch):
    import modules.email_finder as ef
    pool = ef.KeyPool(["k_one_12345678", "k_two_12345678"], "ContactOut", credits_per_key=100, cost_per_use=10, store=tmp_path / "k.json")
    monkeypatch.setattr(ef, "CONTACTOUT_POOL", pool)
    ef._API_CACHE.clear()

    class R:
        status_code = 403; text = ""
        def json(self): return {"message": "API access not enabled for this account"}
    monkeypatch.setattr(ef.requests, "get", lambda *a, **k: R())
    errs = []
    assert ef.enrich_with_contactout("https://www.linkedin.com/in/x", errors=errs) is None
    assert any("HTTP 403" in e and "API access not enabled" in e for e in errs)
    assert all(r["used"] == 0 for r in pool.snapshot())          # credits untouched: it was a refusal, not a spend
    assert "403" in pool.snapshot()[0]["note"]
    assert ef._API_CACHE == {}                                    # errors are never cached
    # explicit "no credits" wording still rotates and marks the key used up
    class R2:
        status_code = 402; text = ""
        def json(self): return {"message": "You have run out of credits"}
    monkeypatch.setattr(ef.requests, "get", lambda *a, **k: R2())
    pool2 = ef.KeyPool(["k_three_1234567"], "ContactOut", credits_per_key=100, cost_per_use=10, store=None)
    monkeypatch.setattr(ef, "CONTACTOUT_POOL", pool2)
    ef.enrich_with_contactout("https://www.linkedin.com/in/y", errors=[])
    assert pool2.snapshot()[0]["remaining"] == 0


def test_probe_and_old_store_migration(tmp_path, monkeypatch):
    import json, modules.email_finder as ef
    class R:
        def __init__(s, c): s.status_code = c; s.text = ""
        def json(s): return {"message": "nope"}
    monkeypatch.setattr(ef.requests, "get", lambda *a, **k: R(404))
    assert ef.probe_contactout("abcdefgh")["ok"] is True          # accepted key, profile simply not found
    monkeypatch.setattr(ef.requests, "get", lambda *a, **k: R(403))
    r = ef.probe_contactout("abcdefgh")
    assert r["ok"] is False and r["http"] == 403
    store = tmp_path / "api_keys.json"                             # file written by the old version: everything "used"
    store.write_text(json.dumps({"added": ["k_old_12345678"], "used": {ef.key_id("k_old_12345678"): 100}}))
    pool = ef.KeyPool([], "ContactOut", credits_per_key=100, cost_per_use=10, store=store)
    assert pool.snapshot()[0]["remaining"] == 100                  # wrongly-marked keys are usable again
