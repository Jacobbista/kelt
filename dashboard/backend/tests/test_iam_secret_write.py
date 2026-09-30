import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.services import iam_service


class WriteSecretTest(unittest.TestCase):
    """A rotation rewrites .testbed.secrets whole and keeps the version it
    replaced as .testbed.secrets.prev (kelt restore secrets), both 0600."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.file = self.dir / ".testbed.secrets"
        self.file.write_text("CAMARA_CLIENT_SECRET=old\nOTHER=keep\n")
        os.chmod(self.file, 0o600)
        patcher = mock.patch.object(iam_service, "TESTBED_SECRETS", self.file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_replaced_version_is_kept_as_prev(self):
        iam_service.IamService._write_secret("CAMARA_CLIENT_SECRET", "new")
        self.assertEqual(self.file.read_text(), "CAMARA_CLIENT_SECRET=new\nOTHER=keep\n")
        prev = self.dir / ".testbed.secrets.prev"
        self.assertEqual(prev.read_text(), "CAMARA_CLIENT_SECRET=old\nOTHER=keep\n")
        self.assertEqual(prev.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)

    def test_no_temporary_file_is_left(self):
        iam_service.IamService._write_secret("CAMARA_CLIENT_SECRET", "new")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), [".testbed.secrets", ".testbed.secrets.prev"])


if __name__ == "__main__":
    unittest.main()
