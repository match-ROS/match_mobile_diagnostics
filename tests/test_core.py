"""Hardware-free regression tests for host, Dashboard, evidence and SSH boundaries."""
import contextlib
from datetime import datetime, timezone
import json
import socket
import subprocess
import threading
import unittest
from unittest.mock import patch

from match_mobile_diagnostics import __version__
from match_mobile_diagnostics import dashboard, engine, host, logs, models
from match_mobile_diagnostics.profiles import load_profile


def by_id(results, check_id):
    return next(item for item in results if item["id"] == check_id)


def healthy_observation(**answers):
    values = {
        "PolyscopeVersion": "URSoftware 3.15.8",
        "robotmode": "Robotmode: RUNNING",
        "safetystatus": "Safetystatus: NORMAL",
        "safetymode": "Safetymode: NORMAL",
        "programState": "PLAYING arbitrary_program.urp",
        "is in remote control": "true",
        "get loaded program": "Loaded program: arbitrary_program.urp",
    }
    values.update(answers)
    return {"reachable": True, "answers": values, "error": "",
            "banner": "Connected: Universal Robots Dashboard Server",
            "observed_at": "2026-09-29T12:00:00+00:00"}


@contextlib.contextmanager
def fake_dashboard(answers):
    """One loopback-only server; records every command sent by the real client."""
    seen, failures = [], []
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(3)

    def serve():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(3)
                connection.sendall(b"Connected: Universal Robots Dashboard Server\n")
                with connection.makefile("rb") as reader:
                    for _ in dashboard.QUERIES:
                        line = reader.readline()
                        if not line:
                            break
                        command = line.decode("ascii").strip()
                        seen.append(command)
                        payload = answers.get(command, "Unknown command").encode("ascii") + b"\n"
                        connection.sendall(payload[:3])
                        connection.sendall(payload[3:])
        except Exception as exc:
            failures.append(exc)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield listener.getsockname()[1], seen
    finally:
        listener.close()
        worker.join(timeout=4)
        if worker.is_alive():
            raise AssertionError("loopback Dashboard test server did not stop")
        if failures:
            raise AssertionError(f"loopback Dashboard server failed: {failures}")


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile("mur620d")

    def evaluate(self, observation=None, mode="operational"):
        return dashboard.evaluate_dashboard("l", self.profile, observation or healthy_observation(), mode)[0]

    def test_real_client_sends_only_allowlisted_queries(self):
        answers = healthy_observation()["answers"]
        with fake_dashboard(answers) as (port, seen):
            observation = dashboard.query_dashboard("127.0.0.1", port=port, timeout=0.5)
        self.assertEqual(seen, list(dashboard.QUERIES))
        self.assertTrue(observation["reachable"])
        self.assertEqual(observation["error"], "")
        self.assertEqual(observation["answers"], answers)
        self.assertEqual(by_id(self.evaluate(observation), "ur.l.dashboard")["status"], "pass")

    def test_unreachable_dashboard_never_implies_remote_or_safety_state(self):
        with patch.object(dashboard.socket, "create_connection", side_effect=ConnectionRefusedError("refused")):
            observation = dashboard.query_dashboard("never-contact.invalid")
        checks = self.evaluate(observation)
        self.assertEqual(by_id(checks, "ur.l.dashboard")["status"], "fail")
        for suffix in ("remote", "safety", "mode", "program"):
            self.assertEqual(by_id(checks, "ur.l." + suffix)["status"], "unknown")

    def test_remote_false_is_distinct_from_network_failure(self):
        checks = self.evaluate(healthy_observation(**{"is in remote control": "Remote control: false"}))
        self.assertEqual(by_id(checks, "ur.l.dashboard")["status"], "pass")
        self.assertEqual(by_id(checks, "ur.l.remote")["status"], "fail")
        self.assertEqual(by_id(checks, "ur.l.safety")["status"], "pass")

    def test_safety_stops_are_distinct_from_network_failure(self):
        for safety in ("ROBOT_EMERGENCY_STOP", "SYSTEM_EMERGENCY_STOP", "PROTECTIVE_STOP", "SAFEGUARD_STOP", "FAULT"):
            with self.subTest(safety=safety):
                checks = self.evaluate(healthy_observation(safetystatus="Safetystatus: " + safety))
                self.assertEqual(by_id(checks, "ur.l.dashboard")["status"], "pass")
                self.assertEqual(by_id(checks, "ur.l.safety")["status"], "fail")
                self.assertIn(safety, by_id(checks, "ur.l.safety")["actual"])

    def test_cb3_unsupported_remote_is_not_applicable_and_safety_falls_back(self):
        checks = self.evaluate(healthy_observation(**{
            "is in remote control": "Unknown command: is in remote control",
            "safetystatus": "Command not supported",
            "safetymode": "Safetymode: NORMAL",
        }))
        self.assertEqual(by_id(checks, "ur.l.remote")["status"], "not_applicable")
        self.assertEqual(by_id(checks, "ur.l.safety")["status"], "pass")

    def test_both_unsupported_safety_queries_leave_safety_unknown(self):
        checks = self.evaluate(healthy_observation(safetystatus="Unknown command", safetymode="Not supported"))
        self.assertEqual(by_id(checks, "ur.l.safety")["status"], "unknown")

    def test_power_off_stopped_program_is_allowed_only_in_preflight(self):
        observation = healthy_observation(robotmode="Robotmode: POWER_OFF", programState="STOPPED")
        preflight = self.evaluate(observation, mode="preflight")
        operational = self.evaluate(observation)
        for suffix in ("mode", "program"):
            self.assertEqual(by_id(preflight, "ur.l." + suffix)["status"], "pass")
            self.assertEqual(by_id(operational, "ur.l." + suffix)["status"], "fail")

    def test_preflight_does_not_pass_unready_or_unrecognized_robot_modes(self):
        for mode in ("BOOTING", "NO_CONTROLLER", "DISCONNECTED", "unexpected response"):
            with self.subTest(mode=mode):
                checks = self.evaluate(healthy_observation(robotmode="Robotmode: " + mode), mode="preflight")
                self.assertNotEqual(by_id(checks, "ur.l.mode")["status"], "pass")

    def test_unrecognized_program_state_never_passes_preflight(self):
        checks = self.evaluate(healthy_observation(programState="garbled response"), mode="preflight")
        self.assertNotEqual(by_id(checks, "ur.l.program")["status"], "pass")

    def test_partial_query_failure_does_not_get_all_clear(self):
        observation = healthy_observation()
        observation["error"] = "Dashboard-Verbindung geschlossen"
        observation["answers"] = {"robotmode": "Robotmode: RUNNING"}
        checks = self.evaluate(observation)
        self.assertEqual(by_id(checks, "ur.l.dashboard")["status"], "fail")
        self.assertEqual(by_id(checks, "ur.l.safety")["status"], "unknown")


class HostTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile("mur620a")

    def test_wrong_reverse_source_address_is_explained(self):
        routes = [{"dev": self.profile["robot_interface"], "prefsrc": "192.168.12.18"}]
        check = host.evaluate_route("l", self.profile, routes)
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["expected"]["src"], "192.168.12.69")
        self.assertEqual(check["actual"]["src"], "192.168.12.18")
        self.assertTrue(check["next_steps"])

    def test_correct_route_and_missing_route_are_distinguished(self):
        routes = [{"dev": self.profile["robot_interface"], "prefsrc": "192.168.12.69"}]
        self.assertEqual(host.evaluate_route("l", self.profile, routes)["status"], "pass")
        self.assertEqual(host.evaluate_route("l", self.profile, [])["status"], "fail")
        self.assertEqual(host.evaluate_route("l", self.profile, None, "ip missing")["status"], "unknown")

    def test_correct_address_on_wrong_interface_fails(self):
        check = host.evaluate_route("l", self.profile, [{"dev": "wrong0", "src": "192.168.12.69"}])
        self.assertEqual(check["status"], "fail")

    def can_checks(self, state="ERROR-ACTIVE", bitrate=250000, flags=None):
        can = [{"flags": ["UP"] if flags is None else flags,
                "linkinfo": {"info_data": {"state": state, "bittiming": {"bitrate": bitrate}}}}]
        missing = {"returncode": 127, "stdout": "", "stderr": "mocked unavailable command"}

        def json_response(argv):
            return (can, {"returncode": 0, "stdout": "", "stderr": ""}) if argv[:4] == ["ip", "-d", "-j", "link"] else (None, missing)

        with patch.object(host, "json_command", side_effect=json_response), \
             patch.object(host, "command", return_value=missing), \
             patch.object(host.Path, "exists", return_value=False), \
             patch.object(host.Path, "is_file", return_value=True), \
             patch.object(host.platform, "release", return_value="6.8-test"), \
             patch.object(host.platform, "version", return_value="test"), \
             patch.object(host.resource, "getrlimit", return_value=(0, 0)):
            return host.collect_host(self.profile, "/fake-workspace")

    def test_can_error_active_means_normal_operation(self):
        check = by_id(self.can_checks(), "battery.can")
        self.assertEqual(check["status"], "pass")
        self.assertEqual(check["actual"]["state"], "ERROR-ACTIVE")

    def test_bus_off_down_wrong_bitrate_or_unknown_state_never_pass(self):
        cases = ({"state": "BUS-OFF"}, {"state": "STOPPED"}, {"flags": []},
                 {"bitrate": 500000}, {"state": None}, {"state": "ERROR-PASSIVE"},
                 {"state": "ERROR-WARNING"})
        for options in cases:
            with self.subTest(options=options):
                self.assertNotEqual(by_id(self.can_checks(**options), "battery.can")["status"], "pass")

    def test_degraded_can_warns_and_unobserved_can_state_is_unknown(self):
        for state, expected in (("ERROR-PASSIVE", "warn"), ("ERROR-WARNING", "warn"), (None, "unknown")):
            with self.subTest(state=state):
                self.assertEqual(by_id(self.can_checks(state=state), "battery.can")["status"], expected)
        self.assertEqual(by_id(self.can_checks(bitrate=None), "battery.can")["status"], "unknown")

    def test_no_lifts_are_not_hardware_failures(self):
        checks = self.can_checks()
        self.assertEqual(by_id(checks, "lift.l.device")["status"], "not_applicable")
        self.assertEqual(by_id(checks, "lift.r.device")["status"], "not_applicable")

    def test_listener_alone_is_not_an_established_reverse_connection(self):
        text = "LISTEN 0 1 0.0.0.0:50005 0.0.0.0:*\n"
        with patch.object(host, "command", return_value={"returncode": 0, "stdout": text, "stderr": ""}):
            checks = host.collect_sockets(self.profile, "operational")
        check = by_id(checks, "ur.l.reverse")
        self.assertEqual(check["status"], "fail")
        self.assertTrue(check["actual"]["listener"])
        self.assertFalse(check["actual"]["connected"])

    def test_reverse_connection_requires_correct_peer(self):
        for peer, expected in (("192.168.12.89", "pass"), ("192.168.12.90", "fail")):
            with self.subTest(peer=peer), patch.object(host, "command", return_value={
                "returncode": 0, "stdout": f"ESTAB 0 0 192.168.12.69:50005 {peer}:40960\n", "stderr": ""
            }):
                check = by_id(host.collect_sockets(self.profile, "operational"), "ur.l.reverse")
                self.assertEqual(check["status"], expected)

    def test_missing_reverse_is_allowed_preflight_but_missing_ss_is_unknown(self):
        for rc, expected in ((0, "pass"), (127, "unknown")):
            with self.subTest(rc=rc), patch.object(host, "command", return_value={"returncode": rc, "stdout": "", "stderr": ""}):
                check = by_id(host.collect_sockets(self.profile, "preflight"), "ur.l.reverse")
                self.assertEqual(check["status"], expected)


class EvidenceAndAggregationTests(unittest.TestCase):
    def test_old_undated_and_future_errors_are_not_current_faults(self):
        now = 1790000000.0
        text = "\n".join((f"[{now-3600}] Failed to cycle2", "Failed to cycle2",
                          f"[{now+60}] Failed to cycle2", "2026-09-29 12:00:00 Failed to cycle2"))
        checks = logs.analyze_log(text, "fixture", now=now)
        self.assertEqual([item["id"] for item in checks], ["logs.window"])
        self.assertEqual(checks[0]["actual"]["historical_or_undated_matches"], 4)

    def test_recent_errors_are_evidence_warnings_not_current_state_assertions(self):
        now = 1790000000.0
        checks = logs.analyze_log(f"[{now-20}] Connection to reverse interface dropped", "fixture", now=now)
        check = by_id(checks, "logs.reverse")
        self.assertEqual(check["status"], "warn")
        self.assertTrue(check["evidence"])
        self.assertIn("Statusabfragen", check["causes"][0])

    def test_successful_reverse_messages_do_not_create_warnings(self):
        now = 1790000000.0
        text = f"[{now}] Robot requested program\n[{now}] Robot connected to reverse interface"
        self.assertFalse(any(item["status"] != "pass" for item in logs.analyze_log(text, "fixture", now)))

    def test_timestamp_requires_zone_for_wall_time(self):
        self.assertIsNone(logs.line_timestamp("2026-09-29 14:00:00 message"))
        expected = datetime(2026, 9, 29, 12, tzinfo=timezone.utc).timestamp()
        self.assertEqual(logs.line_timestamp("2026-09-29T14:00:00+02:00 message"), expected)
        self.assertEqual(logs.line_timestamp("2026-09-29T12:00:00Z message"), expected)

    def report(self, statuses):
        report = models.new_report("mur620a", "/mur620", "operational", "fixture")
        report["results"] = [models.result(str(index), "fixture", status, status) for index, status in enumerate(statuses)]
        return models.finalize(report)

    def test_unknown_empty_and_only_inapplicable_never_produce_all_clear(self):
        for statuses in ([], ["unknown"], ["pass", "unknown"], ["not_applicable"]):
            with self.subTest(statuses=statuses):
                report = self.report(statuses)
                self.assertFalse(report["complete"])
                self.assertNotIn("Alle vorgesehenen", report["summary"])
                self.assertEqual(models.exit_code(report), 2)

    def test_success_warnings_and_failures_have_distinct_exit_codes(self):
        self.assertEqual(models.exit_code(self.report(["pass", "not_applicable"])), 0)
        self.assertEqual(models.exit_code(self.report(["pass", "warn"])), 1)
        self.assertEqual(models.exit_code(self.report(["fail", "unknown"])), 1)

    def test_malformed_results_and_unknown_statuses_are_rejected(self):
        for checks in (None, [{}], ["bad check"], [{"status": "unexpected"}]):
            report = models.new_report("mur620a", "/mur620", "operational", "fixture")
            report["results"] = checks
            with self.subTest(checks=checks), self.assertRaises(ValueError):
                models.finalize(report)

    def test_credentials_are_redacted_from_structured_and_log_evidence(self):
        evidence = {"password": "secret123", "nested": ["Authorization: Basic fixtureCredential",
                    "Authorization: Bearer fixtureToken", "https://fixtureUser:fixturePass@example.invalid/path"]}
        encoded = json.dumps(models.redact(evidence))
        for credential in ("secret123", "fixtureCredential", "fixtureToken", "fixturePass"):
            self.assertNotIn(credential, encoded)


class EngineBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.robot = "mur620d"
        self.profile = load_profile(self.robot)

    def remote_report(self, results=None):
        report = models.new_report(self.robot, "/mur620", "operational", self.robot)
        report["results"] = results if results is not None else [
            models.result("host.identity", "Verbindung", "pass", "confirmed", actual=self.robot)]
        return models.finalize(report)

    def test_wrong_local_identity_prevents_robot_probes(self):
        with patch.object(engine.socket, "gethostname", return_value="operator-laptop"), \
             patch.object(engine, "query_dashboard") as dashboard_probe, \
             patch.object(engine, "collect_host") as host_probe, \
             patch.object(engine, "collect_ros") as ros_probe:
            report = engine.local_scan(self.robot)
        self.assertEqual(by_id(report["results"], "host.identity")["status"], "fail")
        self.assertFalse(report["complete"])
        dashboard_probe.assert_not_called()
        host_probe.assert_not_called()
        ros_probe.assert_not_called()

    def test_arbitrary_playing_program_does_not_hide_missing_reverse_connection(self):
        reverse = [models.result("ur.l.reverse", "UR links", "fail", "missing", actual={"connected": False})]
        with patch.object(engine.socket, "gethostname", return_value=self.robot), \
             patch.object(engine, "query_dashboard", return_value=healthy_observation()), \
             patch.object(engine, "collect_sockets", return_value=reverse):
            report = engine.local_scan(self.robot, host_results=[], ros_data={"results": [], "stats": {}})
        self.assertEqual(by_id(report["results"], "ur.l.program")["status"], "pass")
        self.assertEqual(by_id(report["results"], "ur.l.reverse")["status"], "fail")
        self.assertEqual(models.exit_code(report), 1)

    def test_failed_network_prerequisite_suppresses_duplicate_connection_alarms(self):
        route = host.evaluate_route("l", self.profile, [{"dev": self.profile["robot_interface"], "src": "192.168.12.18"}])
        reverse = [models.result("ur.l.reverse", "UR links", "fail", "missing")]
        with patch.object(engine.socket, "gethostname", return_value=self.robot), \
             patch.object(engine, "query_dashboard", return_value={"reachable": False, "answers": {}, "error": "timeout"}), \
             patch.object(engine, "collect_sockets", return_value=reverse):
            report = engine.local_scan(self.robot, host_results=[route], ros_data={"results": [], "stats": {}})
        self.assertEqual(by_id(report["results"], "network.l.route")["status"], "fail")
        for suffix in ("dashboard", "reverse"):
            check = by_id(report["results"], "ur.l." + suffix)
            self.assertEqual(check["status"], "unknown")
            self.assertIn("network.l.route", check["causes"][0])

    def test_ssh_connection_missing_installation_and_timeout_are_incomplete(self):
        cases = ((255, "", "Permission denied"), (127, "", "MUR_DIAGNOSTICS_NOT_INSTALLED"))
        for response, check_id in zip(cases, ("ssh.connection", "ssh.installation")):
            with self.subTest(check_id=check_id), patch.object(engine.processes, "run", return_value=response):
                report = engine.remote_scan(self.robot)
                self.assertEqual(by_id(report["results"], check_id)["status"], "unknown")
                self.assertFalse(report["complete"])
        with patch.object(engine.processes, "run", side_effect=subprocess.TimeoutExpired("ssh", 2)):
            self.assertFalse(engine.remote_scan(self.robot)["complete"])

    def test_ssh_uses_batch_mode_without_install_or_deploy_actions(self):
        data = self.remote_report()
        with patch.object(engine.processes, "run", return_value=(0, json.dumps(data), "")) as command:
            report = engine.remote_scan(self.robot, host="rosmatch@robot-alias")
        argv = command.call_args.args[0]
        self.assertEqual(argv[0], "ssh")
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("rosmatch@robot-alias", argv)
        self.assertNotIn("sudo", argv[-1])
        self.assertNotIn("git clone", argv[-1])
        self.assertEqual(report["target"], "rosmatch@robot-alias")

    def test_ssh_different_versions_are_reported(self):
        data = self.remote_report()
        data["tool_version"] = "older-fixture-version"
        with patch.object(engine.processes, "run", return_value=(0, json.dumps(data), "")):
            report = engine.remote_scan(self.robot)
        check = by_id(report["results"], "ssh.version")
        self.assertEqual(check["status"], "warn")
        self.assertEqual(check["expected"], __version__)

    def test_ssh_wrong_robot_or_schema_is_rejected(self):
        for updates in ({"robot": "mur620a"}, {"schema_version": 999}):
            data = self.remote_report()
            data.update(updates)
            with self.subTest(updates=updates), patch.object(engine.processes, "run", return_value=(0, json.dumps(data), "")):
                report = engine.remote_scan(self.robot)
                self.assertFalse(report["complete"])
                self.assertEqual(by_id(report["results"], "ssh.report")["status"], "unknown")

    def test_ssh_malformed_result_items_return_unknown_instead_of_crashing(self):
        for malformed in ([{}], ["not a check"], [{"status": "unexpected"}]):
            data = self.remote_report()
            data["results"] = malformed
            with self.subTest(malformed=malformed), patch.object(engine.processes, "run", return_value=(0, json.dumps(data), "")):
                report = engine.remote_scan(self.robot)
                self.assertFalse(report["complete"])
                self.assertEqual(by_id(report["results"], "ssh.report")["status"], "unknown")

    def test_invalid_ssh_target_is_rejected_before_process_launch(self):
        with patch.object(engine.processes, "run") as command:
            with self.assertRaises(ValueError):
                engine.remote_scan(self.robot, host="robot; touch /tmp/unwanted")
        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
