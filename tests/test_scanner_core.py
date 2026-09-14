import unittest
from scanner_core import (
    validate_email_target,
    validate_username_target,
    generate_export_text,
    format_telegram_report,
)

class TestScannerCore(unittest.TestCase):
    def test_validate_email_target(self):
        # Valid emails
        norm, err = validate_email_target("Test.User@example.com")
        self.assertIsNone(err)
        self.assertEqual(norm, "Test.User@example.com")

        norm, err = validate_email_target("  alice@domain.org  ")
        self.assertIsNone(err)
        self.assertEqual(norm, "alice@domain.org")

        # Invalid emails
        norm, err = validate_email_target("not-an-email")
        self.assertIsNotNone(err)
        self.assertIsNone(norm)

        norm, err = validate_email_target("")
        self.assertIsNotNone(err)
        self.assertIsNone(norm)

    def test_validate_username_target(self):
        # Valid usernames
        user, err = validate_username_target("johndoe")
        self.assertIsNone(err)
        self.assertEqual(user, "johndoe")

        # Strip leading @
        user, err = validate_username_target("@cool_dev")
        self.assertIsNone(err)
        self.assertEqual(user, "cool_dev")

        # Too short
        user, err = validate_username_target("a")
        self.assertIsNotNone(err)

        # Contains spaces
        user, err = validate_username_target("john doe")
        self.assertIsNotNone(err)

    def test_format_telegram_report_empty(self):
        msg, export_txt = format_telegram_report("user@example.com", "Holehe", [])
        self.assertIsNone(export_txt)
        self.assertIn("No confirmed accounts were found", msg)
        self.assertIn("user@example.com", msg)

    def test_format_telegram_report_found_holehe(self):
        found = ["GitHub", "Twitter", "Spotify"]
        msg, export_txt = format_telegram_report("user@example.com", "Holehe (Found Only)", found, elapsed_seconds=2.4)
        self.assertIsNone(export_txt)
        self.assertIn("Found Accounts:</b> 3", msg)
        self.assertIn("• <b>GitHub</b>", msg)
        self.assertIn("• <b>Twitter</b>", msg)
        self.assertIn("• <b>Spotify</b>", msg)

    def test_format_telegram_report_found_user_scanner(self):
        found = [
            {"site_name": "GitHub", "category": "Development", "url": "https://github.com/user"},
            {"site_name": "Reddit", "category": "Social", "url": "https://reddit.com/user/user"},
        ]
        msg, export_txt = format_telegram_report("user", "User Scanner", found)
        self.assertIsNone(export_txt)
        self.assertIn("Found Accounts:</b> 2", msg)
        self.assertIn("<a href=\"https://github.com/user\">GitHub</a>", msg)
        self.assertIn("<i>Development</i>", msg)

    def test_format_telegram_report_overflow(self):
        # Simulate 100 found items to exceed 3800 chars
        found = [
            {"site_name": f"SiteNumber{i}", "category": "TestingCategoryLongName", "url": f"https://sitenumber{i}.com/profiles/useraccountname"}
            for i in range(120)
        ]
        msg, export_txt = format_telegram_report("user", "User Scanner", found)
        self.assertIsNotNone(export_txt)
        self.assertIn("Full report attached below", msg)
        self.assertIn("Total Found: 120", export_txt)

    def test_generate_export_text(self):
        items = [
            {"site_name": "GitHub", "category": "Code", "url": "https://github.com/test"},
            "Twitter",
        ]
        txt = generate_export_text("test@example.com", "Unified Engine", items)
        self.assertIn("Target: test@example.com", txt)
        self.assertIn("• [Code] GitHub - https://github.com/test", txt)
        self.assertIn("• Twitter", txt)

if __name__ == "__main__":
    unittest.main()
