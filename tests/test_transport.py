"""Streaming and CLI tests use local Python children, never SSH or robot hardware."""
import contextlib
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from match_mobile_diagnostics import engine, models, processes
from match_mobile_diagnostics.profiles import load_profile


ROOT = Path(__file__).resolve().parents[1]


def report(robot="mur620d"):
    value = models.new_report(robot, "/mur620", "operational", robot)
    value["results"] = [models.result("host.identity", "Verbindung", "pass", "Host bestätigt", actual=robot)]
    value["stats"] = {"mir_battery": {"text": "76 %", "percent": 76.0, "age_seconds": 0.5,
                                        "source": "/mur620/battery_state"}}
    return models.finalize(value)


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not predicate():
        raise AssertionError("Local test child did not reach the expected state")


@contextlib.contextmanager
def python_children(script):
    """Replace process spawn only; retain production process-group ownership."""
    original_spawn = processes.spawn
    children = []

    def spawn(_argv, **kwargs):
        child = original_spawn([sys.executable, "-u", "-c", script], **kwargs)
        children.append(child)
        return child

    with patch.object(engine.processes, "spawn", side_effect=spawn):
        try:
            yield children
        finally:
            for child in children:
                processes.stop(child)
                for stream in (child.stdin, child.stdout, child.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()


class RemoteWatchTests(unittest.TestCase):
    def watch(self):
        return engine.remote_watch(robot="mur620d", host="fixture-host", duration=0.1, interval=0.1)

    def test_jsonl_reports_are_followed_by_incomplete_disconnect_without_stale_cards(self):
        first = report()
        second = copy.deepcopy(first)
        second["stats"]["mir_battery"]["text"] = "75 %"
        script = f"print({json.dumps(first)!r}, flush=True); print({json.dumps(second)!r}, flush=True)"
        with python_children(script) as children:
            values = list(self.watch())
            self.assertGreaterEqual(len(values), 2)
            # A latest-only transport may intentionally coalesce the first frame.
            self.assertEqual(values[-2]["stats"]["mir_battery"]["text"], "75 %")
            self.assertEqual(values[-1]["results"][0]["id"], "ssh.connection")
            self.assertEqual(values[-1]["results"][0]["status"], "unknown")
            self.assertFalse(values[-1]["complete"])
            self.assertEqual(values[-1]["stats"], {})
            self.assertIsNotNone(children[0].poll())

    def test_invalid_json_wrong_robot_and_malformed_checks_become_unknown(self):
        wrong_robot = report("mur620c")
        malformed = report()
        malformed["results"] = ["not a check"]
        for payload in ("not json", json.dumps(wrong_robot), json.dumps(malformed), "[]"):
            with self.subTest(payload=payload):
                script = f"print({payload!r}, flush=True)"
                with python_children(script):
                    values = list(self.watch())
                self.assertEqual(len(values), 1)
                self.assertEqual(values[0]["results"][0]["status"], "unknown")
                self.assertFalse(values[0]["complete"])
                self.assertEqual(values[0]["stats"], {})

    def test_generator_close_terminates_long_lived_child(self):
        script = f"import time; print({json.dumps(report())!r}, flush=True); time.sleep(60)"
        with python_children(script) as children:
            stream = self.watch()
            try:
                self.assertTrue(next(stream)["complete"])
                child = children[0]
                self.assertIsNone(child.poll())
            finally:
                stream.close()
            self.assertIsNotNone(child.poll())
            self.assertNotIn(child, processes._children)

    def test_invalid_payload_then_close_also_cleans_up_live_child(self):
        with python_children("import time; print('broken', flush=True); time.sleep(60)") as children:
            stream = self.watch()
            try:
                self.assertFalse(next(stream)["complete"])
            finally:
                stream.close()
            self.assertIsNotNone(children[0].poll())
            self.assertNotIn(children[0], processes._children)

    def test_stderr_disconnect_reason_is_captured_and_redacted(self):
        script = "import sys; print('Authorization: Basic fixtureCredential', file=sys.stderr, flush=True); sys.exit(255)"
        with python_children(script):
            values = list(self.watch())
        encoded = json.dumps(values)
        self.assertNotIn("fixtureCredential", encoded)
        self.assertFalse(values[-1]["complete"])


class RosStreamTests(unittest.TestCase):
    @staticmethod
    def sample():
        return {"namespace": "/mur620", "results": [models.result(
            "ros.mir_battery", "Batterien", "pass", "Batterie verfügbar", age_seconds=1.0)],
                "stats": {"mir_battery": {"text": "76 %", "percent": 76.0,
                                           "age_seconds": 1.0, "source": "/mur620/battery_state"}}}

    def make_stream(self):
        return engine.RosStream(load_profile("mur620d"), "/mur620", "/fake-workspace", "operational", 0.1)

    def test_input_profile_is_delivered_and_close_terminates_collector(self):
        sample = self.sample()
        script = (
            "import sys,json,time; profile=json.loads(sys.stdin.read()); "
            f"sample=json.loads({json.dumps(sample)!r}); "
            "sample['stats']['profile_robot']={'text':profile['robot']}; "
            "print(json.dumps(sample), flush=True); time.sleep(60)"
        )
        with python_children(script) as children:
            stream = self.make_stream()
            try:
                wait_until(lambda: stream.latest is not None)
                self.assertEqual(stream.snapshot()["stats"]["profile_robot"]["text"], "mur620d")
            finally:
                stream.close()
            self.assertIsNotNone(children[0].poll())
            self.assertFalse(stream.reader.is_alive())

    def test_cached_snapshot_age_advances_without_mutating_source(self):
        script = f"import sys,time; sys.stdin.read(); print({json.dumps(self.sample())!r}, flush=True); time.sleep(60)"
        with python_children(script):
            stream = self.make_stream()
            try:
                wait_until(lambda: stream.latest is not None)
                with patch.object(engine.time, "monotonic", return_value=stream.updated + 4.0):
                    snapshot = stream.snapshot()
                self.assertEqual(snapshot["stats"]["mir_battery"]["age_seconds"], 5.0)
                self.assertEqual(snapshot["results"][0]["age_seconds"], 5.0)
                self.assertEqual(stream.latest["stats"]["mir_battery"]["age_seconds"], 1.0)
            finally:
                stream.close()

    def test_terminated_collector_invalidates_even_recent_cached_success(self):
        script = f"import sys; sys.stdin.read(); print({json.dumps(self.sample())!r}, flush=True)"
        with python_children(script) as children:
            stream = self.make_stream()
            try:
                wait_until(lambda: stream.latest is not None and children[0].poll() is not None)
                snapshot = stream.snapshot()
                self.assertEqual(snapshot["stats"], {})
                self.assertEqual(snapshot["results"][0]["status"], "unknown")
            finally:
                stream.close()

    def test_expired_stream_cannot_reuse_old_telemetry(self):
        script = f"import sys,time; sys.stdin.read(); print({json.dumps(self.sample())!r}, flush=True); time.sleep(60)"
        with python_children(script):
            stream = self.make_stream()
            try:
                wait_until(lambda: stream.latest is not None)
                with patch.object(engine.time, "monotonic", return_value=stream.updated + 7.0):
                    snapshot = stream.snapshot()
                self.assertEqual(snapshot["stats"], {})
                self.assertEqual(snapshot["results"][0]["status"], "unknown")
            finally:
                stream.close()


class LocalScanTimingTests(unittest.TestCase):
    def test_supplier_is_sampled_after_both_dashboards_finish(self):
        profile = load_profile("mur620d")
        expected_addresses = {arm["address"] for arm in profile["arms"].values()}
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        finished, calls = set(), []
        latest = {"namespace": "/mur620", "results": [models.result(
            "ros.mir_battery", "mir_battery", "unknown", "old sample", age_seconds=60.0)],
            "stats": {"mir_battery": {"text": "Old 10 %", "percent": 10.0,
                                       "age_seconds": 60.0, "status": "unknown"}}}

        def dashboard_query(address):
            # Both independent Dashboard jobs must actually run before either
            # can finish. Completing the jobs models newer ROS data arriving.
            barrier.wait(timeout=2)
            with lock:
                finished.add(address)
                calls.append("dashboard:" + address)
                if finished == expected_addresses:
                    latest["results"] = [models.result("ros.mir_battery", "mir_battery", "pass",
                        "new sample", age_seconds=0.1, actual={"percent": 88.0})]
                    latest["stats"]["mir_battery"] = {"text": "88 %", "percent": 88.0,
                        "age_seconds": 0.1, "status": "pass"}
            return {"reachable": True, "error": "", "observed_at": models.utc_now(), "answers": {
                "robotmode": "Robotmode: RUNNING", "safetystatus": "Safetystatus: NORMAL",
                "is in remote control": "true", "programState": "PLAYING external_control.urp",
                "get loaded program": "Loaded program: external_control.urp"}}

        def supplier():
            with lock:
                self.assertEqual(finished, expected_addresses,
                                 "ROS snapshot was sampled before Dashboard jobs completed")
                calls.append("supplier")
                return copy.deepcopy(latest)

        with patch.object(engine.socket, "gethostname", return_value="mur620d"), \
             patch.object(engine, "query_dashboard", side_effect=dashboard_query), \
             patch.object(engine, "collect_sockets", return_value=[]), \
             patch.object(engine, "RosStream") as unexpected_stream, \
             patch.object(engine, "collect_ros") as unexpected_collector, \
             patch.dict(os.environ, {"ROS_DOMAIN_ID": "17"}):
            value = engine.local_scan("mur620d", namespace="/mur620", host_results=[],
                                      domain_id=62, ros_supplier=supplier)
        self.assertEqual(calls[-1], "supplier")
        self.assertEqual(calls.count("supplier"), 1)
        self.assertEqual(value["stats"]["mir_battery"]["percent"], 88.0)
        self.assertEqual(value["stats"]["mir_battery"]["status"], "pass")
        self.assertLess(value["stats"]["mir_battery"]["age_seconds"], 1.0)
        self.assertEqual(next(item for item in value["results"]
                              if item["id"] == "ros.mir_battery")["status"], "pass")
        unexpected_stream.assert_not_called()
        unexpected_collector.assert_not_called()


class PayloadExpiryTests(unittest.TestCase):
    def test_expired_lift_is_unknown_with_history_but_no_current_height(self):
        value = report()
        value["results"] = [models.result("ros.lift_l", "lift_l", "pass", "Hardware aktuell",
            age_seconds=0.5, actual={"height_m": 0.3, "last_success_age_sec": 1.0})]
        value["stats"] = {"lift_l": {"text": "0.300 m", "height_m": 0.3, "status": "pass",
                                     "communication_confirmed": True, "age_seconds": 0.5}}
        aged = models.finalize(engine.age_payload(value, 1.2))
        self.assertFalse(aged["complete"])
        self.assertEqual(aged["results"][0]["status"], "unknown")
        self.assertAlmostEqual(aged["results"][0]["actual"]["last_success_age_sec"], 2.2)
        stat = aged["stats"]["lift_l"]
        self.assertEqual(stat["status"], "unknown")
        self.assertIsNone(stat["height_m"])
        self.assertFalse(stat["communication_confirmed"])
        self.assertTrue(stat["text"].startswith("Veraltet"))
        self.assertEqual(stat["last_observed"]["height_m"], 0.3)

    def test_expired_failure_becomes_historical_evidence_not_current_fault(self):
        value = report()
        value["results"] = [models.result("ur.l.safety", "UR links", "fail", "PROTECTIVE_STOP",
            age_seconds=5.0, actual="PROTECTIVE_STOP")]
        aged = models.finalize(engine.age_payload(value, 2.0))
        check = aged["results"][0]
        self.assertEqual(check["status"], "unknown")
        self.assertEqual(check["previous_status"], "fail")
        self.assertEqual(check["actual"], "PROTECTIVE_STOP")
        self.assertFalse(aged["complete"])

    def test_profile_freshness_override_and_inapplicable_signals_are_respected(self):
        value = report()
        value["profile"] = {"freshness": {"mir_battery": 20.0}}
        value["results"] = [models.result("ros.mir_battery", "mir_battery", "pass", "available", age_seconds=9.0),
                            models.result("ros.lift_l", "lift_l", "not_applicable", "no lift")]
        value["stats"]["mir_battery"]["age_seconds"] = 9.0
        aged = engine.age_payload(value, 2.0)
        self.assertEqual(aged["results"][0]["status"], "pass")
        self.assertEqual(aged["stats"]["mir_battery"]["percent"], 76.0)
        self.assertEqual(aged["results"][1]["status"], "not_applicable")

    def test_controller_timeout_differs_from_ur_heartbeat_timeout(self):
        value = report()
        value["results"] = [models.result("ros.ur_l.state", "ur_l", "pass", "live", age_seconds=5.0),
                            models.result("ros.ur_l.controllers", "ur_l", "pass", "active", age_seconds=5.0)]
        aged = engine.age_payload(value, 2.0)
        self.assertEqual(aged["results"][0]["status"], "unknown")
        self.assertEqual(aged["results"][1]["status"], "pass")

    def test_repeated_aging_does_not_duplicate_history_or_stale_prefix(self):
        value = report()
        value["stats"]["mir_battery"]["age_seconds"] = 9.0
        engine.age_payload(value, 2.0)
        engine.age_payload(value, 2.0)
        stat = value["stats"]["mir_battery"]
        self.assertEqual(stat["text"].count("Veraltet"), 1)
        self.assertNotIn("last_observed", stat["last_observed"])
        self.assertEqual(stat["last_observed"]["percent"], 76.0)
        self.assertIsNone(stat["percent"])


class CliTransportTests(unittest.TestCase):
    def test_invalid_scan_watch_and_gui_arguments_exit_three_without_probes(self):
        cases = (
            ["scan"],
            ["scan", "--robot", "not-a-robot"],
            ["scan", "--robot", "mur620d", "--duration", "nan"],
            ["scan", "--robot", "mur620d", "--duration", "0"],
            ["scan", "--robot", "mur620d", "--namespace", "bad namespace"],
            ["watch", "--robot", "mur620d", "--format", "json"],
            ["gui", "--unexpected"],
        )
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT)}
        for args in cases:
            with self.subTest(args=args):
                response = subprocess.run(
                    [sys.executable, "-m", "match_mobile_diagnostics.cli", *args],
                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(response.returncode, 3, response.stderr)
                self.assertEqual(response.stdout, "")
                self.assertIn("usage:", response.stderr.lower())
                self.assertNotIn("Traceback", response.stderr)

    def test_requested_domain_is_forwarded_without_changing_session_environment(self):
        with patch.dict(os.environ, {"ROS_DOMAIN_ID": "17"}):
            argv, env = engine.ros_command("/fake-workspace", "/mur620", "operational", 0.1, domain_id=62)
            self.assertIn("--domain-id 62", argv[-1])
            self.assertEqual(env["ROS_DOMAIN_ID"], "17")
            self.assertEqual(os.environ["ROS_DOMAIN_ID"], "17")

    def test_remote_command_forwards_profile_domain_to_selected_robot(self):
        target, argv = engine.ssh_command("mur620d")
        self.assertEqual(target, "mur620d")
        self.assertIn("--domain-id 62", argv[-1])
        self.assertIn("--via local", argv[-1])


if __name__ == "__main__":
    unittest.main()
