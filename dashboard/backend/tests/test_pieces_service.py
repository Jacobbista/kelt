import json
import unittest
from unittest import mock

from app.services import pieces_service as ps

OP = "20260926-221248-577734-ran_link"
REG = {"ran_link": {"title": "Bring the RAN link up", "runs": [{"phase": "04-overlay-network", "tags": ["ran_link"]}],
                    "tier": "disrupt", "changes": "c", "stops": "s", "takes_s": 60, "check": "ran_link_up"}}


class PiecesServiceTest(unittest.TestCase):
    def test_registry_is_public_fields_only(self):
        with mock.patch.object(ps, "_registry", return_value=REG):
            self.assertEqual(ps.list_pieces()["ran_link"],
                             {"title": "Bring the RAN link up", "tier": "disrupt", "changes": "c", "stops": "s", "takes_s": 60,
                              "confirm_word": None})

    def test_unknown_piece_is_refused_without_calling_the_runner(self):
        with mock.patch.object(ps, "_registry", return_value=REG), mock.patch.object(ps, "_runner") as run:
            with self.assertRaises(KeyError):
                ps.start("../x", user="a")
            run.assert_not_called()

    def test_start_passes_source_and_user(self):
        with mock.patch.object(ps, "_registry", return_value=REG), \
             mock.patch.object(ps, "_runner", return_value='{"id": "%s", "state": "started"}' % OP) as run:
            self.assertEqual(ps.start("ran_link", user="jacopo"), {"id": OP, "state": "started"})
            self.assertEqual(run.call_args[0][0], ["start", "ran_link", "--source", "dashboard", "--user=jacopo"])
            ps.start("ran_link", user="-rf")  # a name that looks like an option stays a value
            self.assertEqual(run.call_args[0][0][-1], "--user=-rf")

    def test_check_runs_once_after_a_clean_exit_and_is_stored(self):
        rec = {"id": OP, "piece": "ran_link", "state": "done", "exit": 0}
        checks = {"ran_link_up": mock.Mock(return_value=(True, "enp0s9 is up"))}
        with mock.patch.object(ps, "_registry", return_value=REG), \
             mock.patch.object(ps, "_runner", return_value=json.dumps({**rec, "log_tail": ""})), \
             mock.patch.object(ps, "_store_check") as store:
            out = ps.get_operation(OP, checks=checks)
        self.assertEqual(out["check"], {"ok": True, "message": "enp0s9 is up"})
        store.assert_called_once()

    def test_exit_zero_with_a_failed_check_reads_failed(self):
        rec = {"id": OP, "piece": "ran_link", "state": "done", "exit": 0}
        checks = {"ran_link_up": mock.Mock(return_value=(False, "enp0s9 is still down"))}
        with mock.patch.object(ps, "_registry", return_value=REG), \
             mock.patch.object(ps, "_runner", return_value=json.dumps({**rec, "log_tail": ""})), \
             mock.patch.object(ps, "_store_check"):
            out = ps.get_operation(OP, checks=checks)
        self.assertEqual((out["state"], out["check"]["ok"]), ("failed", False))

    def test_a_stored_check_is_not_run_again(self):
        rec = {"id": OP, "piece": "ran_link", "state": "done", "exit": 0, "check": {"ok": True, "message": "m"}}
        check = mock.Mock()
        with mock.patch.object(ps, "_registry", return_value=REG), \
             mock.patch.object(ps, "_runner", return_value=json.dumps({**rec, "log_tail": ""})):
            ps.get_operation(OP, checks={"ran_link_up": check})
        check.assert_not_called()

    def test_a_malformed_operation_id_is_refused_without_calling_the_runner(self):
        with mock.patch.object(ps, "_runner") as run:
            for bad in ("i1", "../x", "20260926-221248-577734-ran_link;rm"):
                with self.assertRaises(KeyError):
                    ps.get_operation(bad, checks={})
            run.assert_not_called()
    def test_a_run_opened_long_after_it_ended_is_not_checked_now(self):
        rec = {"id": OP, "piece": "ran_link", "state": "done", "exit": 0, "ended": "2026-09-20T10:00:00Z"}
        check = mock.Mock(return_value=(False, "down now"))
        with mock.patch.object(ps, "_registry", return_value=REG), \
             mock.patch.object(ps, "_runner", return_value=json.dumps({**rec, "log_tail": ""})), \
             mock.patch.object(ps, "_store_check"):
            out = ps.get_operation(OP, checks={"ran_link_up": check}, now=ps._parse_ts("2026-09-27T10:00:00Z"))
        check.assert_not_called()
        self.assertEqual(out["state"], "done")
        self.assertIsNone(out["check"]["ok"])

    def test_a_run_opened_right_after_it_ended_is_checked(self):
        rec = {"id": OP, "piece": "ran_link", "state": "done", "exit": 0, "ended": "2026-09-27T10:00:00Z"}
        check = mock.Mock(return_value=(True, "up"))
        with mock.patch.object(ps, "_registry", return_value=REG), \
             mock.patch.object(ps, "_runner", return_value=json.dumps({**rec, "log_tail": ""})), \
             mock.patch.object(ps, "_store_check"):
            out = ps.get_operation(OP, checks={"ran_link_up": check}, now=ps._parse_ts("2026-09-27T10:02:00Z"))
        self.assertEqual(out["check"], {"ok": True, "message": "up"})

    def test_store_check_replaces_the_record_atomically(self):
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        (d / f"{OP}.json").write_text(json.dumps({"id": OP, "state": "done"}))
        with mock.patch.object(ps, "OPS_DIR", d), mock.patch.object(ps.os, "replace", wraps=ps.os.replace) as rep:
            ps._store_check({"id": OP, "check": {"ok": True, "message": "m"}})
        rep.assert_called_once()
        self.assertEqual(json.loads((d / f"{OP}.json").read_text())["check"]["ok"], True)


    def test_retention_merges_limits_and_usage(self):
        answers = {"retention": '{"max_age_days": 30, "max_mb": 50}',
                   "usage": '{"runs": 3, "bytes": 1200, "oldest": "2026-09-26T22:12:33Z"}'}
        with mock.patch.object(ps, "_runner", side_effect=lambda args, **k: answers[args[0]]):
            self.assertEqual(ps.retention(), {"max_age_days": 30, "max_mb": 50, "runs": 3, "bytes": 1200,
                                              "oldest": "2026-09-26T22:12:33Z"})

    def test_set_retention_passes_whole_numbers_only(self):
        with mock.patch.object(ps, "_runner") as run:
            for bad in ((0, 50), (30, 10241), ("7", 50), (True, 50)):
                with self.assertRaises(ValueError):
                    ps.set_retention(*bad)
            run.assert_not_called()

    def test_set_retention_calls_the_runner(self):
        answers = {"retention": '{"max_age_days": 7, "max_mb": 20}', "usage": '{"runs": 0, "bytes": 0, "oldest": null}'}
        with mock.patch.object(ps, "_runner", side_effect=lambda args, **k: answers[args[0]]) as run:
            out = ps.set_retention(7, 20)
        self.assertEqual(run.call_args_list[0][0][0], ["retention", "--max-age-days", "7", "--max-mb", "20", "--json"])
        self.assertEqual((out["max_age_days"], out["max_mb"]), (7, 20))

    def test_retention_route_is_matched_before_the_operation_id(self):
        from app.routers.pieces import router
        paths = [r.path for r in router.routes]
        self.assertLess(paths.index("/api/v1/operations/retention"), paths.index("/api/v1/operations/{op_id}"))


    def test_list_marks_runs_whose_piece_reads_back(self):
        recs = [{"id": OP, "piece": "ran_link", "state": "done", "exit": 0},
                {"id": "20260926-221248-577735-other", "piece": "other", "state": "done", "exit": 0}]
        with mock.patch.object(ps, "_registry", return_value={**REG, "other": {"title": "o"}}), \
             mock.patch.object(ps, "_runner", return_value=json.dumps(recs)):
            out = ps.list_operations()
        self.assertEqual([r["reads_back"] for r in out], [True, False])

    def test_list_shows_a_failed_read_back_as_failed(self):
        recs = [{"id": OP, "piece": "ran_link", "state": "done", "exit": 0, "check": {"ok": False, "message": "down"}}]
        with mock.patch.object(ps, "_registry", return_value=REG), mock.patch.object(ps, "_runner", return_value=json.dumps(recs)):
            self.assertEqual(ps.list_operations()[0]["state"], "failed")

    def test_list_asks_for_the_newest_runs_it_shows(self):
        with mock.patch.object(ps, "_registry", return_value=REG), mock.patch.object(ps, "_runner", return_value="[]") as run:
            ps.list_operations("running")
        self.assertEqual(run.call_args[0][0], ["list", "--json", "--limit", str(ps.LIST_LIMIT), "--state", "running"])


    def test_a_settling_check_is_not_stored_and_reads_pending(self):
        rec = {"id": OP, "piece": "ran_link", "state": "done", "exit": 0}
        checks = {"ran_link_up": mock.Mock(return_value=(None, "the AMF is still being replaced"))}
        with mock.patch.object(ps, "_registry", return_value=REG), \
             mock.patch.object(ps, "_runner", return_value=json.dumps({**rec, "log_tail": ""})), \
             mock.patch.object(ps, "_store_check") as store:
            out = ps.get_operation(OP, checks=checks)
        self.assertEqual(out["check"], {"ok": None, "pending": True, "message": "the AMF is still being replaced"})
        self.assertEqual(out["state"], "done")
        store.assert_not_called()


    def test_start_passes_the_typed_confirmation_word(self):
        with mock.patch.object(ps, "_registry", return_value=REG), \
             mock.patch.object(ps, "_runner", return_value='{"id": "%s", "state": "started"}' % OP) as run:
            ps.start("ran_link", user="jacopo", confirm="detach")
        self.assertEqual(run.call_args[0][0], ["start", "ran_link", "--source", "dashboard", "--user=jacopo", "--confirm=detach"])


if __name__ == "__main__":
    unittest.main()
