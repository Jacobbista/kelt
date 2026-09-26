import json
import os
import tempfile
import unittest

from lib import runmeta


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_open_then_close(self):
        runmeta.open_window(self.dir, "load")
        runmeta.close_window(self.dir, "load")
        [w] = runmeta.windows(self.dir)
        self.assertEqual(w["label"], "load")
        self.assertTrue(w["start_utc"].endswith("Z"))
        self.assertTrue(w["end_utc"].endswith("Z"))

    def test_unclosed_window_has_no_end(self):
        runmeta.open_window(self.dir, "load")
        self.assertIsNone(runmeta.windows(self.dir)[0]["end_utc"])

    def test_close_without_open_fails(self):
        with self.assertRaises(KeyError):
            runmeta.close_window(self.dir, "nope")

    def test_discarded_are_kept(self):
        runmeta.add_discarded(self.dir, "rep 2", "session lost")
        self.assertEqual(runmeta.discarded(self.dir), [{"what": "rep 2", "reason": "session lost"}])
        with open(os.path.join(self.dir, "window.json")) as fh:
            self.assertIn("discarded", json.load(fh))
