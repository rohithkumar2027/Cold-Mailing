"""
Automated unit tests for recruiter lead generation modules
"""
import unittest
from modules.email_finder import clean_person_name, resolve_company_domain, generate_email_permutations, find_recruiter_email
from modules.scraper import parse_linkedin_title, build_search_query
from modules.email_templates import generate_email_draft
from modules.sheets_manager import SheetsManager


class TestRecruiterLeadGen(unittest.TestCase):
    def test_clean_person_name(self):
        # Case 1: Simple name
        first, last = clean_person_name("Jane Doe")
        self.assertEqual((first, last), ("jane", "doe"))

        # Case 2: Name with certifications
        first, last = clean_person_name("John Smith, PHR, SHRM-CP")
        self.assertEqual((first, last), ("john", "smith"))

        # Case 3: Name with pronouns
        first, last = clean_person_name("Alice Johnson (She/Her)")
        self.assertEqual((first, last), ("alice", "johnson"))

        # Case 4: Name with trailing separator
        first, last = clean_person_name("Robert Vance - Recruiter at Google")
        self.assertEqual((first, last), ("robert", "vance"))

    def test_resolve_company_domain(self):
        # Known companies
        domain, pattern = resolve_company_domain("Google")
        self.assertEqual(domain, "google.com")
        self.assertEqual(pattern, "first.last")

        domain, pattern = resolve_company_domain("Meta")
        self.assertEqual(domain, "meta.com")
        self.assertEqual(pattern, "flast")

        domain, pattern = resolve_company_domain("Stripe")
        self.assertEqual(domain, "stripe.com")
        self.assertEqual(pattern, "first")

        # Unknown company with suffix
        domain, pattern = resolve_company_domain("Acme Technologies Inc")
        self.assertEqual(domain, "acme.com")

    def test_generate_email_permutations(self):
        perms = generate_email_permutations("john", "doe", "example.com")
        emails = [e for e, _ in perms]
        self.assertIn("john.doe@example.com", emails)
        self.assertIn("jdoe@example.com", emails)
        self.assertIn("john@example.com", emails)

    def test_find_recruiter_email(self):
        # Emails are never guessed: the result is either a real published address or "Not Found".
        res = find_recruiter_email("Jane Doe", "Google")
        self.assertEqual(res["domain"], "google.com")
        if res["primary_email"] == "Not Found":
            self.assertEqual(res["confidence"], "None")
        else:
            self.assertTrue(res["confidence"].startswith("Published"))

    def test_parse_linkedin_title(self):
        title = "Jane Doe - Senior Technical Recruiter - Google | LinkedIn"
        parsed = parse_linkedin_title(title, "Google")
        self.assertEqual(parsed["name"], "Jane Doe")
        self.assertIn("Recruiter", parsed["title"])

    def test_build_search_query(self):
        query_swe = build_search_query("Google", "swe")
        self.assertIn("linkedin.com/in", query_swe)
        self.assertIn("Google", query_swe)
        self.assertIn("technical recruiter", query_swe.lower())

        query_ai = build_search_query("Meta", "ai_ml")
        self.assertIn("Meta", query_ai)
        self.assertTrue("ai" in query_ai.lower() or "machine learning" in query_ai.lower())

    def test_generate_email_draft(self):
        draft_swe = generate_email_draft("Jane Doe", "Google", role_category="swe", candidate_name="Alex Smith")
        self.assertIn("Alex Smith", draft_swe["subject"])
        self.assertIn("Jane", draft_swe["body"])
        self.assertIn("Google", draft_swe["body"])
        self.assertIn("Software Engineering", draft_swe["subject"])

        draft_ai = generate_email_draft("Sarah Connor", "OpenAI", role_category="ai_ml")
        self.assertIn("Machine Learning", draft_ai["subject"])
        self.assertIn("PyTorch", draft_ai["body"])

    def test_sheets_manager_csv_fallback(self):
        # Write to a throw-away folder so test data never lands in the real data/leads_master.csv
        import tempfile
        from pathlib import Path
        from unittest import mock
        import modules.sheets_manager as sheets_module
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(sheets_module, "DATA_DIR", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

        manager = SheetsManager(sheet_name="Test_Sheet", credentials_file="nonexistent.json")
        self.assertFalse(manager.is_connected)
        
        sample_lead = [{
            "name": "Test Recruiter",
            "primary_email": "test.recruiter@example.com",
            "company": "TestCorp",
            "title": "Technical Recruiter",
            "role_category": "swe",
            "confidence": "High",
            "linkedin_url": "https://www.linkedin.com/in/test-recruiter-12345",
            "alternative_emails": ["trecruiter@example.com"],
        }]
        res = manager.save_leads(sample_lead)
        self.assertEqual(res["added"], 1)
        self.assertTrue(res["csv_path"].endswith("leads_master.csv"))


if __name__ == "__main__":
    unittest.main()
