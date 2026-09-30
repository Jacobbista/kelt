import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "kelt-piece"


def load_runner():
    loader = importlib.machinery.SourceFileLoader("kelt_piece", str(RUNNER))
    spec = importlib.util.spec_from_loader("kelt_piece", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


STUB = textwrap.dedent("""\
    #!/usr/bin/env python3
    import os, sys, time
    open(os.environ["STUB_ARGS"], "a").write(" ".join(sys.argv[1:]) + "\\n")
    open(os.environ["STUB_ENV"], "w").write(os.environ.get("SECRET_TOKEN", ""))
    print("PLAY [RAN link] ***")
    print("TASK [Read the RAN link state] ***")
    print("ok: [master]")
    print("TASK [Restart the worker network setup] ***")
    time.sleep(float(os.environ.get("STUB_SLEEP", "0")))
    sys.exit(int(os.environ.get("STUB_EXIT", "0")))
""")


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "ansible" / "phases" / "04-overlay-network").mkdir(parents=True)
        (self.tmp / "ansible" / "phases" / "04-overlay-network" / "playbook.yml").write_text("- hosts: all\n")
        stub = self.tmp / "ansible-playbook"
        stub.write_text(STUB)
        stub.chmod(0o755)
        (self.tmp / "pieces.yml").write_text(textwrap.dedent("""\
            ran_link:
              title: Bring the RAN link up
              runs: [{phase: 04-overlay-network, tags: [ran_link]}]
              extra_vars: {foo: bar}
              tier: disrupt
              changes: c
              stops: s
              takes_s: 60
              check: ran_link_up
            attach:
              title: Attach
              runs: [{phase: 04-overlay-network, tags: [overlay, nad]}]
              set_env: {PHYSICAL_RAN_ENABLED: "true"}
              tier: disrupt
              changes: c
              stops: s
            """))
        (self.tmp / "env").write_text("DASHBOARD_DEV_ENABLED=true\nPHYSICAL_RAN_ENABLED=false\n")
        (self.tmp / "secrets").write_text("SECRET_TOKEN=hunter2-not-for-logs\n")
        self.env = {
            **os.environ,
            "KELT_OPS_DIR": str(self.tmp / "ops"),
            "KELT_PIECES_FILE": str(self.tmp / "pieces.yml"),
            "KELT_ENV_FILE": str(self.tmp / "env"),
            "KELT_SECRETS_FILE": str(self.tmp / "secrets"),
            "KELT_ANSIBLE_DIR": str(self.tmp / "ansible"),
            "KELT_ANSIBLE_PLAYBOOK": str(stub),
            "STUB_ARGS": str(self.tmp / "args"),
            "STUB_ENV": str(self.tmp / "seen_env"),
        }

    def run_cli(self, *args, **env):
        return subprocess.run([sys.executable, str(RUNNER), *args], env={**self.env, **env},
                              capture_output=True, text=True, timeout=60)

    def records(self):
        return [json.loads(p.read_text()) for p in sorted((self.tmp / "ops").glob("*.json"))]

    def test_registry_rejects_disrupt_without_stops(self):
        kp = load_runner()
        with self.assertRaises(ValueError):
            kp.validate({"x": {"title": "t", "runs": [{"phase": "p", "tags": ["a"]}], "tier": "disrupt", "changes": "c"}})

    def test_unknown_or_unsafe_piece_is_refused_before_running(self):
        for name in ("nope", "../etc", "ran_link;rm"):
            r = self.run_cli("run", name)
            self.assertNotEqual(r.returncode, 0, name)
        self.assertFalse((self.tmp / "args").exists())

    def test_run_writes_a_record_with_steps_and_the_same_command_as_run_phase(self):
        r = self.run_cli("run", "ran_link", "--source", "cli", "--user", "jacopo")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = self.records()[0]
        self.assertEqual((rec["piece"], rec["source"], rec["user"], rec["state"], rec["exit"]),
                         ("ran_link", "cli", "jacopo", "done", 0))
        self.assertEqual(rec["steps"], ["Read the RAN link state", "Restart the worker network setup"])
        args = (self.tmp / "args").read_text().split()
        self.assertEqual(args[0], "phases/04-overlay-network/playbook.yml")
        self.assertIn("dashboard_dev_enabled=true", args)
        self.assertIn("foo=bar", args)
        self.assertEqual(args[args.index("--tags") + 1], "ran_link")
        self.assertTrue((self.tmp / "ops" / f"{rec['id']}.log.gz").exists())

    def test_secrets_are_loaded_but_never_recorded(self):
        self.run_cli("run", "ran_link")
        self.assertEqual((self.tmp / "seen_env").read_text(), "hunter2-not-for-logs")
        for p in (self.tmp / "ops").iterdir():
            data = p.read_bytes()
            if p.suffix == ".gz":
                import gzip
                data = gzip.decompress(data)
            self.assertNotIn(b"hunter2", data, p.name)

    def test_failed_playbook_is_a_failed_record(self):
        r = self.run_cli("run", "ran_link", STUB_EXIT="2")
        self.assertEqual(r.returncode, 2)
        self.assertEqual((self.records()[0]["state"], self.records()[0]["exit"]), ("failed", 2))

    def test_set_env_is_written_to_the_env_file(self):
        self.run_cli("run", "attach")
        env = (self.tmp / "env").read_text().splitlines()
        self.assertIn("PHYSICAL_RAN_ENABLED=true", env)
        self.assertIn("DASHBOARD_DEV_ENABLED=true", env)

    def test_set_env_keeps_the_version_it_replaced_as_prev(self):
        before = (self.tmp / "env").read_text()
        os.chmod(self.tmp / "env", 0o640)
        self.run_cli("run", "attach")
        self.assertEqual((self.tmp / "env.prev").read_text(), before)
        self.assertEqual((self.tmp / "env.prev").stat().st_mode & 0o777, 0o640)
        self.run_cli("run", "attach")
        self.assertEqual((self.tmp / "env.prev").read_text(), before, "an unchanged write keeps .prev")

    def test_second_start_returns_the_running_id(self):
        first = json.loads(self.run_cli("start", "ran_link", "--source", "dashboard", "--user", "a", STUB_SLEEP="3", KELT_LAUNCH="direct").stdout)
        self.assertEqual(first["state"], "started")
        second = json.loads(self.run_cli("start", "ran_link", "--source", "dashboard", "--user", "b").stdout)
        self.assertEqual(second, {"id": first["id"], "state": "running"})
        for _ in range(50):
            if self.records()[0]["state"] != "running":
                break
            time.sleep(0.2)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.records()[0]["state"], "done")

    def test_a_record_whose_process_is_gone_reads_as_interrupted(self):
        ops = self.tmp / "ops"
        ops.mkdir()
        (ops / "20260101-000000-ran_link.json").write_text(json.dumps(
            {"id": "20260101-000000-ran_link", "piece": "ran_link", "state": "running", "pid": 999999,
             "started": "2026-01-01T00:00:00Z", "ended": None, "exit": None, "steps": []}))
        out = json.loads(self.run_cli("list", "--json").stdout)
        self.assertEqual(out[0]["state"], "interrupted")

    def test_retention_prunes_old_records_but_never_a_running_one(self):
        kp = load_runner()
        kp.PIECES = self.tmp / "pieces.yml"  # the test's registry, not the checkout's
        ops = self.tmp / "ops"
        ops.mkdir()
        old = time.time() - 40 * 86400
        import fcntl
        holder = os.open(ops / ".ran_link.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)  # a run really holding the piece
        os.write(holder, b"b-old-running")
        self.addCleanup(os.close, holder)
        for rid, state in (("a-old", "done"), ("b-old-running", "running")):
            (ops / f"{rid}.json").write_text(json.dumps({"id": rid, "piece": "ran_link", "state": state}))
            (ops / f"{rid}.log.gz").write_bytes(b"x")
            for p in ops.glob(f"{rid}.*"):
                os.utime(p, (old, old))
        kp.prune(ops, max_age_days=30, max_mb=50)
        self.assertFalse((ops / "a-old.json").exists())
        self.assertFalse((ops / "a-old.log.gz").exists())
        self.assertTrue((ops / "b-old-running.json").exists())

    def test_refused_start_names_the_holder_even_before_its_record_exists(self):
        import fcntl
        ops = self.tmp / "ops"
        ops.mkdir()
        fd = os.open(ops / ".ran_link.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, b"20260927-100000-000001-ran_link")
        try:
            out = json.loads(self.run_cli("start", "ran_link", "--source", "dashboard", "--user", "a").stdout)
        finally:
            os.close(fd)
        self.assertEqual(out, {"id": "20260927-100000-000001-ran_link", "state": "running"})

    def test_a_runner_error_is_a_failed_record_with_its_reason(self):
        r = self.run_cli("run", "ran_link", KELT_ANSIBLE_PLAYBOOK=str(self.tmp / "no-such-playbook"))
        self.assertNotEqual(r.returncode, 0)
        rec = self.records()[0]
        self.assertEqual(rec["state"], "failed")
        self.assertIn("no-such-playbook", rec.get("error", ""))
        self.assertFalse(list((self.tmp / "ops").glob("*.log")))
        self.assertTrue(list((self.tmp / "ops").glob("*.log.gz")))

    def test_a_start_whose_run_never_begins_reports_failed(self):
        out = json.loads(self.run_cli("start", "ran_link", "--source", "dashboard", "--user", "a",
                                      KELT_LAUNCH="none", KELT_START_WAIT_S="0.5").stdout)
        self.assertEqual(out["state"], "failed")
        self.assertTrue(out["id"])

    def test_the_dashboard_launch_leaves_the_callers_cgroup(self):
        kp = load_runner()
        rid = "20260927-100000-000001-ran_link"
        cmd = kp.launch_command("ran_link", rid, "jacopo")
        self.assertEqual(cmd[:3], ["sudo", "-n", "systemd-run"])
        self.assertIn(f"--unit=kelt-piece-{rid}", cmd)
        self.assertIn(f"--uid={os.getuid()}", cmd)
        tail = cmd[cmd.index("--") + 1:]
        self.assertEqual(tail[1:], [str(RUNNER), "run", "ran_link", "--source", "dashboard", "--user=jacopo", f"--id={rid}"])
    def test_a_running_record_without_its_lock_is_interrupted_even_if_the_pid_lives(self):
        ops = self.tmp / "ops"
        ops.mkdir()
        (ops / "20260101-000000-000000-ran_link.json").write_text(json.dumps(
            {"id": "20260101-000000-000000-ran_link", "piece": "ran_link", "state": "running",
             "pid": os.getpid(), "steps": []}))
        out = json.loads(self.run_cli("list", "--json").stdout)
        self.assertEqual(out[0]["state"], "interrupted")

    def test_env_files_may_use_export(self):
        (self.tmp / "env").write_text("export DASHBOARD_DEV_ENABLED=true\n")
        self.run_cli("run", "ran_link")
        self.assertIn("dashboard_dev_enabled=true", (self.tmp / "args").read_text().split())

    def test_the_operations_directory_is_private(self):
        self.run_cli("run", "ran_link")
        self.assertEqual((self.tmp / "ops").stat().st_mode & 0o777, 0o700)

    def test_prune_keeps_the_run_that_just_ended(self):
        kp = load_runner()
        ops = self.tmp / "ops"
        ops.mkdir()
        (ops / "new.json").write_text(json.dumps({"id": "new", "state": "done"}))
        (ops / "new.log.gz").write_bytes(b"x" * 2048)
        kp.prune(ops, max_age_days=30, max_mb=0.001, keep="new")
        self.assertTrue((ops / "new.log.gz").exists())

    def test_pieces_lists_the_registry(self):
        out = json.loads(self.run_cli("pieces", "--json").stdout)
        self.assertEqual(sorted(out), ["attach", "ran_link"])
        self.assertEqual(out["ran_link"]["tier"], "disrupt")


    def test_usage_on_an_empty_record(self):
        self.assertEqual(json.loads(self.run_cli("usage", "--json").stdout), {"runs": 0, "bytes": 0, "oldest": None})

    def test_usage_counts_runs_and_bytes(self):
        self.run_cli("run", "ran_link")
        u = json.loads(self.run_cli("usage", "--json").stdout)
        self.assertEqual(u["runs"], 1)
        self.assertGreater(u["bytes"], 0)
        self.assertTrue(u["oldest"].endswith("Z"))

    def test_retention_defaults_and_set(self):
        self.assertEqual(json.loads(self.run_cli("retention", "--json").stdout), {"max_age_days": 30, "max_mb": 50})
        out = json.loads(self.run_cli("retention", "--max-age-days", "7", "--max-mb", "20", "--json").stdout)
        self.assertEqual(out, {"max_age_days": 7, "max_mb": 20})
        env = (self.tmp / "env").read_text().splitlines()
        self.assertIn("KELT_OPS_MAX_AGE_DAYS=7", env)
        self.assertIn("KELT_OPS_MAX_MB=20", env)
        self.assertIn("DASHBOARD_DEV_ENABLED=true", env)

    def test_retention_refuses_out_of_bounds_and_leaves_the_env_alone(self):
        before = (self.tmp / "env").read_text()
        for args in (["--max-age-days", "0"], ["--max-mb", "99999"], ["--max-age-days", "x"]):
            r = self.run_cli("retention", *args)
            self.assertEqual(r.returncode, 2, args)
        self.assertEqual((self.tmp / "env").read_text(), before)


    def test_limits_outside_their_bounds_in_the_env_fall_back_to_the_defaults(self):
        (self.tmp / "env").write_text("KELT_OPS_MAX_AGE_DAYS=0\nKELT_OPS_MAX_MB=-5\n")
        self.assertEqual(json.loads(self.run_cli("retention", "--json").stdout), {"max_age_days": 30, "max_mb": 50})


    def group_registry(self):
        """ran_link and attach share the lock group `ran`; attach asks for a typed word."""
        text = (self.tmp / "pieces.yml").read_text()
        text = text.replace("  stops: s\n", "  stops: s\n  lock: ran\n")
        text += "  confirm_word: detach\n"
        (self.tmp / "pieces.yml").write_text(text)

    def hold(self, key, rid):
        import fcntl
        ops = self.tmp / "ops"
        ops.mkdir(exist_ok=True)
        fd = os.open(ops / f".{key}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, rid.encode())
        self.addCleanup(os.close, fd)

    def test_registry_checks_lock_and_confirm_word(self):
        kp = load_runner()
        base = {"title": "t", "runs": [{"phase": "p", "tags": ["a"]}], "tier": "change", "changes": "c"}
        for bad in ({"lock": "../x"}, {"confirm_word": "Detach now"}, {"confirm_word": ""}):
            with self.assertRaises(ValueError, msg=bad):
                kp.validate({"x": {**base, **bad}})
        kp.validate({"x": {**base, "lock": "ran", "confirm_word": "detach"}})

    def test_a_piece_of_the_same_lock_group_is_refused_while_another_runs(self):
        self.group_registry()
        self.hold("ran", "20260927-100000-000001-ran_link")
        out = json.loads(self.run_cli("start", "attach", "--source", "dashboard", "--user", "a", "--confirm", "detach").stdout)
        self.assertEqual(out["state"], "failed")
        self.assertIn("Bring the RAN link up", out["error"])
        r = self.run_cli("run", "attach", "--confirm", "detach")
        self.assertEqual(r.returncode, 75)
        self.assertIn("Bring the RAN link up", r.stderr)
        self.assertFalse((self.tmp / "args").exists())

    def test_the_same_piece_in_a_group_gets_the_running_id(self):
        self.group_registry()
        self.hold("ran", "20260927-100000-000001-ran_link")
        out = json.loads(self.run_cli("start", "ran_link", "--source", "dashboard", "--user", "a").stdout)
        self.assertEqual(out, {"id": "20260927-100000-000001-ran_link", "state": "running"})

    def test_a_run_holding_its_group_lock_reads_as_running(self):
        self.group_registry()
        rid = "20260927-100000-000002-attach"
        self.hold("ran", rid)
        (self.tmp / "ops" / f"{rid}.json").write_text(json.dumps(
            {"id": rid, "piece": "attach", "state": "running", "started": "2026-09-27T10:00:00Z", "steps": []}))
        out = json.loads(self.run_cli("list", "--json").stdout)
        self.assertEqual(out[0]["state"], "running")

    def test_pieces_exposes_the_confirm_word(self):
        self.group_registry()
        out = json.loads(self.run_cli("pieces", "--json").stdout)
        self.assertEqual(out["attach"]["confirm_word"], "detach")
        self.assertIsNone(out["ran_link"]["confirm_word"])


    def precheck_registry(self):
        text = (self.tmp / "pieces.yml").read_text()
        text = text.replace("  set_env: {PHYSICAL_RAN_ENABLED: \"true\"}\n",
                            "  precheck: [{phase: 04-overlay-network, tags: [ran_nic_check]}]\n  set_env: {PHYSICAL_RAN_ENABLED: \"true\"}\n")
        (self.tmp / "pieces.yml").write_text(text)

    def test_a_failing_precheck_leaves_the_env_alone_and_runs_nothing_else(self):
        self.precheck_registry()
        r = self.run_cli("run", "attach", STUB_EXIT="2")
        self.assertEqual(r.returncode, 2)
        self.assertIn("PHYSICAL_RAN_ENABLED=false", (self.tmp / "env").read_text().splitlines())
        calls = (self.tmp / "args").read_text().splitlines()
        self.assertEqual(len(calls), 1)
        self.assertIn("--tags ran_nic_check", calls[0])
        self.assertEqual(self.records()[0]["state"], "failed")

    def test_a_passing_precheck_runs_before_the_env_and_the_runs(self):
        self.precheck_registry()
        r = self.run_cli("run", "attach")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = (self.tmp / "args").read_text().splitlines()
        self.assertEqual([c.split("--tags ")[1] for c in calls], ["ran_nic_check", "overlay,nad"])
        self.assertIn("PHYSICAL_RAN_ENABLED=true", (self.tmp / "env").read_text().splitlines())

    def test_registry_checks_the_precheck_shape(self):
        kp = load_runner()
        base = {"title": "t", "runs": [{"phase": "p", "tags": ["a"]}], "tier": "change", "changes": "c"}
        with self.assertRaises(ValueError):
            kp.validate({"x": {**base, "precheck": [{"phase": "p"}]}})


    def test_a_piece_with_a_confirm_word_runs_only_with_that_word(self):
        self.group_registry()  # attach asks for "detach"
        for extra in ([], ["--confirm", "detac"], ["--confirm=Detach now"]):
            r = self.run_cli("run", "attach", *extra)
            self.assertEqual(r.returncode, 64, extra)
            self.assertIn("detach", r.stderr)
        self.assertFalse((self.tmp / "args").exists())
        out = json.loads(self.run_cli("start", "attach", "--source", "dashboard", "--user", "a").stdout)
        self.assertEqual(out["state"], "failed")
        self.assertIn("detach", out["error"])
        r = self.run_cli("run", "attach", "--confirm", "detach")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_dashboard_launch_carries_the_word(self):
        kp = load_runner()
        cmd = kp.launch_command("attach", "20260927-100000-000001-attach", "a", confirm="detach")
        self.assertEqual(cmd[-1], "--confirm=detach")


if __name__ == "__main__":
    unittest.main()
