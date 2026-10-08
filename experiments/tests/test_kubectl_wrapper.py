import os
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.join(HERE, "..")

# A stand-in for `vagrant ssh master -c ...` that echoes what it reads on stdin.
FAKE_VAGRANT = "#!/bin/bash\necho \"[Testbed] Profile: test\"\ncat\n"


class KubectlWrapperTest(unittest.TestCase):
    def run_bash(self, script):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "vagrant")
            with open(fake, "w") as fh:
                fh.write(FAKE_VAGRANT)
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
            env = {**os.environ, "PATH": f"{d}:{os.environ['PATH']}"}
            env.pop("KELT_KUBECTL", None)
            return subprocess.run(["bash", "-c", f"source lib/common.sh; {script}"], cwd=EXP, env=env,
                                  capture_output=True, text=True, timeout=30).stdout

    def test_a_manifest_on_stdin_reaches_kubectl(self):
        # faults.sh applies its NetworkPolicy with `kubectl apply -f -`.
        self.assertIn("kind: NetworkPolicy", self.run_bash("echo 'kind: NetworkPolicy' | kubectl apply -f -"))

    def test_other_calls_leave_the_caller_stdin_alone(self):
        out = self.run_bash("printf 'one\\ntwo\\n' | { kubectl get pods; cat; }")
        self.assertIn("one", out)
        self.assertIn("two", out)
