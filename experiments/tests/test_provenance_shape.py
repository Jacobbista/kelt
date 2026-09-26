import json
import os
import subprocess
import tempfile
import unittest

EXP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


class ProvenanceShapeTest(unittest.TestCase):
    def test_pilot_flag_and_new_keys(self):
        # KELT_KUBECTL=true turns every kubectl call into an empty no-op, so the
        # script runs without a cluster.
        run_dir = tempfile.mkdtemp()
        env = dict(os.environ, KELT_PILOT="1", KELT_KUBECTL="true", KELT_GATEWAY_URL="http://gw")
        subprocess.run(["bash", os.path.join(EXP, "provenance.sh"), run_dir, "test"],
                       env=env, check=True, capture_output=True)
        with open(os.path.join(run_dir, "provenance.json")) as fh:
            p = json.load(fh)
        self.assertIs(p["pilot"], True)
        self.assertEqual(p["exposure_namespaces"], "positioning|camara")
        self.assertRegex(p["host_tz"], r"^[+-]\d{4}$")
