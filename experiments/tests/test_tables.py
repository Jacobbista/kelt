import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tables  # noqa: E402


def mk(root, slug, stamp, pilot=False, dirty=False):
    d = os.path.join(root, slug, stamp)
    os.makedirs(d)
    with open(os.path.join(d, "provenance.json"), "w") as fh:
        json.dump({"pilot": pilot, "kelt_worktree_dirty": dirty}, fh)
    return d


class SelectTest(unittest.TestCase):
    def test_pilots_never_selected(self):
        root = tempfile.mkdtemp()
        good = mk(root, "resource-use", "20260930T100000Z")
        mk(root, "resource-use", "20260930T110000Z", pilot=True)
        dirty = mk(root, "resource-use", "20260930T120000Z", dirty=True)
        self.assertEqual(tables.select_runs(root, "resource-use", None), [good, dirty])

    def test_listed_runs_only(self):
        root = tempfile.mkdtemp()
        a = mk(root, "resource-use", "20260930T100000Z")
        mk(root, "resource-use", "20260930T130000Z")
        self.assertEqual(tables.select_runs(root, "resource-use", {"resource-use/20260930T100000Z"}), [a])

    def test_run_without_provenance_is_skipped(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "resource-use", "20260930T100000Z"))
        self.assertEqual(tables.select_runs(root, "resource-use", None), [])
