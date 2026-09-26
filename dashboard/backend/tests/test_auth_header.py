import unittest
from unittest import mock

from fastapi import HTTPException

from app import auth


class BearerHeaderTest(unittest.TestCase):
    def assert_401(self, header):
        with mock.patch.object(auth.settings, "skip_auth", False), self.assertRaises(HTTPException) as ctx:
            auth.get_principal(authorization=header, access_token=None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_bearer_without_a_token_is_401(self):
        self.assert_401("Bearer ")
        self.assert_401("Bearer    ")

    def test_no_header_is_401(self):
        self.assert_401(None)


if __name__ == "__main__":
    unittest.main()
