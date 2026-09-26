import unittest

import watchdog


class TokenTest(unittest.TestCase):
    def test_matching_token(self):
        self.assertTrue(watchdog.token_matches("s3cret-value", "s3cret-value"))

    def test_wrong_or_missing_token(self):
        self.assertFalse(watchdog.token_matches("other", "s3cret-value"))
        self.assertFalse(watchdog.token_matches(None, "s3cret-value"))

    def test_no_configured_token_refuses_everything(self):
        self.assertFalse(watchdog.token_matches("", ""))
        self.assertFalse(watchdog.token_matches(None, ""))


if __name__ == "__main__":
    unittest.main()
