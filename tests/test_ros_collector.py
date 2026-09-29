"""Offline regression tests: no robot or ROS installation required."""
import contextlib
import copy
import io
import json
import math
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from match_mobile_diagnostics.ros_collector import (
    collect, discover, environment_domain, evaluate_observations, main, normalize_namespace,
    lift_communication_values, observe_driver_domains, decode_lift_status, watch, _json_safe,
)


class RosEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.profile = {"robot": "mur620c", "has_lifts": True, "bms_id": "0x0240"}
        self.ns = "/mur620c"
        self.obs = {"topics": {}, "controllers": {}, "publishers": {},
                    "nodes": [], "graph_topics": [], "lift_diagnostics": {}}

    def sample(self, value, age=0):
        return {"value": value, "received": self.now-age, "observed_at": "2026-09-29T12:00:00Z"}

    def evaluate(self, **kwargs):
        return evaluate_observations(self.profile, self.obs, now=self.now, **kwargs)

    def check(self, check_id, **kwargs):
        return next(r for r in self.evaluate(**kwargs)["results"] if r["id"] == check_id)

    def healthy_ur(self):
        self.obs["nodes"].append(self.ns + "/UR10_l/controller_manager")
        prefix = self.ns + "/UR10_l/io_and_status_controller/"
        for suffix, value in (("robot_mode", 7), ("safety_mode", 1), ("robot_program_running", True)):
            self.obs["topics"][prefix+suffix] = self.sample(value, 600)
            self.obs["publishers"][prefix+suffix] = 1
        self.obs["topics"][prefix+"io_states"] = self.sample(True)
        self.obs["controllers"]["l"] = self.sample([
            {"name": "io_and_status_controller", "state": "active", "claimed_interfaces": []},
            {"name": "scaled_joint_trajectory_controller", "state": "active", "claimed_interfaces": ["j/position"]}])
        return prefix

    def healthy_lift(self):
        topic = self.ns+"/ewellix_lift_l/state"
        self.obs["topics"][topic] = self.sample({"actual_positions": [322, 323, -1], "errors": [0]})
        self.obs["lift_diagnostics"]["l"] = self.sample({"hardware_comm_ok": "true",
            "last_cycle_ok": "true", "last_success_age_sec": "0.02", "successful_cycles": "200",
            "consecutive_failures": "0", "failure_reason": "", "port": "/dev/serial/by-id/lift"})
        return topic

    def test_fraction_and_percent(self):
        self.obs["topics"][self.ns+"/battery_state"] = self.sample(0.56)
        self.obs["topics"][self.ns+"/bms_status/SOC"] = self.sample(56.0)
        for kind in ("mir_battery", "mur_battery"):
            self.assertEqual(self.evaluate()["stats"][kind]["text"], "56.0 %")

    def test_invalid_battery_never_valid_percent(self):
        for value in (math.nan, math.inf, -1, 56):
            with self.subTest(value=value):
                self.obs["topics"][self.ns+"/battery_state"] = self.sample(value)
                self.assertEqual(self.check("ros.mir_battery")["status"], "unknown")
                self.assertIsNone(self.evaluate()["stats"]["mir_battery"]["percent"])
                json.dumps(_json_safe(self.evaluate()), allow_nan=False)

    def test_low_battery_warning(self):
        self.obs["topics"][self.ns+"/battery_state"] = self.sample(0.08)
        self.assertEqual(self.check("ros.mir_battery")["status"], "warn")

    def test_stale_battery_and_old_header_unknown(self):
        topic = self.ns+"/battery_state"
        self.obs["topics"][topic] = self.sample(0.56, 11)
        self.assertEqual(self.check("ros.mir_battery")["status"], "unknown")
        self.obs["topics"][topic] = {**self.sample(0.56), "header_age_at_receive": 11}
        self.assertEqual(self.check("ros.mir_battery")["status"], "unknown")

    def test_future_header_unknown(self):
        self.obs["topics"][self.ns+"/battery_state"] = {**self.sample(0.56), "clock_invalid": True}
        self.assertEqual(self.check("ros.mir_battery")["status"], "unknown")

    def test_latched_status_unchanged_is_valid_with_live_io(self):
        self.healthy_ur()
        self.assertEqual(self.check("ros.ur_l.state")["status"], "pass")
        self.assertIn("scaled_joint_trajectory_controller", self.evaluate()["stats"]["ur_l"]["text"])

    def test_latched_status_without_heartbeat_unknown(self):
        prefix = self.healthy_ur()
        self.obs["topics"][prefix+"io_states"] = self.sample(True, 7)
        self.assertEqual(self.check("ros.ur_l.state")["status"], "unknown")

    def test_latched_status_without_live_publisher_unknown(self):
        prefix = self.healthy_ur()
        self.obs["publishers"][prefix+"safety_mode"] = 0
        self.assertEqual(self.check("ros.ur_l.state")["status"], "unknown")

    def test_missing_mode_not_assumed_normal(self):
        prefix = self.healthy_ur()
        del self.obs["topics"][prefix+"safety_mode"]
        self.assertEqual(self.check("ros.ur_l.state")["status"], "unknown")

    def test_ur_stop_and_stopped_program_fail(self):
        prefix = self.healthy_ur()
        self.obs["topics"][prefix+"safety_mode"] = self.sample(6)
        self.assertEqual(self.check("ros.ur_l.state")["status"], "fail")
        self.obs["topics"][prefix+"safety_mode"] = self.sample(1)
        self.obs["topics"][prefix+"robot_program_running"] = self.sample(False)
        self.assertEqual(self.check("ros.ur_l.state")["status"], "fail")

    def test_power_off_permitted_only_in_preflight(self):
        prefix = self.healthy_ur()
        self.obs["topics"][prefix+"robot_mode"] = self.sample(3)
        self.obs["topics"][prefix+"robot_program_running"] = self.sample(False)
        self.assertEqual(self.check("ros.ur_l.state", mode="preflight")["status"], "pass")
        self.assertEqual(self.check("ros.ur_l.state")["status"], "fail")

    def test_lift_state_cannot_hide_bad_hardware_cycles(self):
        self.healthy_lift()
        self.obs["lift_diagnostics"]["l"]["value"].update({"hardware_comm_ok": "false", "last_cycle_ok": "false"})
        self.assertEqual(self.check("ros.lift_l")["status"], "fail")

    def test_lift_state_alone_not_proof(self):
        self.healthy_lift()
        self.obs["lift_diagnostics"].clear()
        self.assertEqual(self.check("ros.lift_l")["status"], "unknown")

    def test_lift_never_uses_foreign_or_global_joints(self):
        self.obs["topics"]["/joint_states"] = self.sample({"left_lift_joint": 0.9})
        self.obs["topics"]["/mur620d/ewellix_lift_l/state"] = self.sample({"actual_positions": [999, 999]})
        self.assertIsNone(self.evaluate()["stats"]["lift_l"]["height_m"])

    def test_lift_success_age_includes_receipt_age(self):
        self.healthy_lift()
        self.obs["lift_diagnostics"]["l"]["value"]["last_success_age_sec"] = "1.5"
        self.obs["lift_diagnostics"]["l"]["received"] = self.now-1
        self.assertEqual(self.check("ros.lift_l")["status"], "fail")

    def test_lift_error_history_is_not_current_fault(self):
        topic = self.healthy_lift()
        self.obs["topics"][topic]["value"]["errors"] = [0x400, 0, 0xFFFFFFFF]
        self.assertEqual(self.check("ros.lift_l")["status"], "pass")
        self.assertEqual(self.evaluate()["stats"]["lift_l"]["error_history"], [0x400])
        self.assertTrue(self.check("ros.lift_l")["evidence"])

    def test_lifts_not_applicable(self):
        self.profile["has_lifts"] = False
        self.assertEqual(self.check("ros.lift_l")["status"], "not_applicable")

    def test_ambiguous_namespace_requires_explicit_selection(self):
        self.obs["graph_topics"] = ["/mur620/battery_state"]
        self.obs["topics"]["/mur620/battery_state"] = self.sample(0.77)
        self.assertEqual(self.check("ros.namespace")["status"], "unknown")
        self.assertNotEqual(self.evaluate()["stats"]["mir_battery"]["text"], "77.0 %")
        self.assertEqual(self.check("ros.namespace", namespace="mur620")["status"], "pass")
        self.assertEqual(self.evaluate(namespace="mur620")["stats"]["mir_battery"]["text"], "77.0 %")

    def test_duplicate_nodes_scoped(self):
        self.obs["nodes"] = ["/mur620c/bms_can_node"]*2 + ["/mur620d/mir_bridge"]*2
        self.assertEqual(self.check("ros.duplicate_nodes")["actual"], ["/mur620c/bms_can_node"])

    def test_bms_parameter_mismatch(self):
        self.obs["bms_id"] = self.sample(0x440)
        self.assertEqual(self.check("ros.bms_id")["status"], "fail")
        self.obs["bms_id"] = self.sample(0x240)
        self.assertEqual(self.check("ros.bms_id")["status"], "pass")

    def test_missing_driver_modes(self):
        self.assertEqual(self.check("ros.driver")["status"], "fail")
        self.assertEqual(self.check("ros.driver", mode="preflight")["status"], "pass")

    def test_contract_pure_and_json_safe(self):
        before = copy.deepcopy(self.obs)
        required = {"id", "component", "status", "summary", "expected", "actual", "source", "observed_at",
                    "age_seconds", "evidence", "causes", "next_steps", "knowledge_id"}
        for result in self.evaluate()["results"]:
            self.assertTrue(required <= result.keys())
        self.assertEqual(before, self.obs)
        json.dumps(self.evaluate(), allow_nan=False)

    def test_invalid_inputs_before_ros(self):
        for namespace in ("/", "mur620;cmd", "mur620 c", "../mur620c"):
            with self.assertRaises(ValueError): normalize_namespace(namespace)
        for duration in (0, -1, math.inf, math.nan, 61):
            with self.assertRaises(ValueError): collect(self.profile, duration=duration)
        for interval in (0, 61, math.inf):
            with self.assertRaises(ValueError): next(watch(self.profile, interval=interval))

    def test_cli_one_json(self):
        payload = self.evaluate()
        stdout = io.StringIO()
        with patch("match_mobile_diagnostics.ros_collector.collect", return_value=payload), \
             patch("sys.stdin", io.StringIO(json.dumps(self.profile))), contextlib.redirect_stdout(stdout):
            self.assertEqual(main(["--duration", "0.1"]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), payload)
        self.assertEqual(len(stdout.getvalue().splitlines()), 1)

    def test_missing_ros_import_is_explicit_unknown(self):
        with patch.dict("sys.modules", {"rclpy": None}):
            output = collect(self.profile, duration=0.1)
        self.assertEqual(output["results"][0]["id"], "ros.available")
        self.assertEqual(output["results"][0]["status"], "unknown")
        self.assertEqual(len(output["stats"]), 6)

    def test_stale_bms_parameter_not_current_match(self):
        self.obs["bms_id"] = self.sample(0x240, 11)
        self.assertEqual(self.check("ros.bms_id")["status"], "unknown")

    def test_expired_controller_response_not_presented_as_active(self):
        self.healthy_ur()
        self.obs["controllers"]["l"]["received"] = self.now-11
        stat = self.evaluate()["stats"]["ur_l"]
        self.assertEqual(stat["controllers"], [])
        self.assertIn("Controller unbekannt", stat["text"])

    def test_actual_domain_is_checked_without_fixing_environment(self):
        with patch.dict("os.environ", {"ROS_DOMAIN_ID": "7"}):
            self.assertEqual(environment_domain(), 7)
            self.assertEqual(self.check("ros.domain")["actual"], 7)
            self.assertEqual(self.check("ros.domain")["status"], "fail")
            self.assertEqual(environment_domain(), 7)
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(environment_domain(), 0)
        self.obs["domain_id"] = 62
        self.assertEqual(self.check("ros.domain")["status"], "pass")

    def test_preflight_absent_driver_runtime_is_not_applicable(self):
        output = self.evaluate(mode="preflight")
        for result in output["results"]:
            if result["component"] != "ros":
                self.assertEqual(result["status"], "not_applicable")
        self.obs["errors"] = ["Optionaler ROS-Typ fehlt"]
        self.assertTrue(any(r["status"] == "unknown" for r in self.evaluate(mode="preflight")["results"]))

    def test_discovery_unavailable_without_ros(self):
        with patch.dict("sys.modules", {"rclpy": None}), patch.dict("os.environ", {"ROS_DOMAIN_ID": "7"}):
            result = discover(self.profile, duration=0.1)
        self.assertFalse(result["available"])
        self.assertFalse(result["visible"])
        self.assertEqual(result["namespace"], self.ns)
        self.assertEqual(result["domain_id"], 7)

    def test_cli_discovery_only(self):
        payload = {"available": True, "visible": False, "domain_id": 0, "namespace": self.ns}
        stdout = io.StringIO()
        with patch("match_mobile_diagnostics.ros_collector.discover", return_value=payload) as query, \
             patch("sys.stdin", io.StringIO(json.dumps(self.profile))), contextlib.redirect_stdout(stdout):
            self.assertEqual(main(["--duration", "0.1", "--discovery-only"]), 0)
        self.assertEqual(json.loads(stdout.getvalue()), payload)
        query.assert_called_once_with(self.profile, None, 0.1, None)

    def test_diagnostic_identity_requires_exact_robot_side_and_node(self):
        expected = self.ns + "/ewellix_lift_l/ewellix_node/communication"
        status = {"name": expected, "hardware_id": "/dev/ttyUSB0", "values": {"hardware_comm_ok": "true"}}
        values = lift_communication_values(self.ns, "l", [status])
        self.assertEqual(values["port"], "/dev/ttyUSB0")
        for wrong in (expected.replace("mur620c", "mur620d"), expected.replace("lift_l", "lift_r"),
                      expected.replace("ewellix_node", "other_node"), "/communication"):
            self.assertIsNone(lift_communication_values(self.ns, "l", [{**status, "name": wrong}]))
        self.assertIsNone(lift_communication_values(self.ns, "l", [status, status]))

    def test_application_topic_alone_is_not_hardware_driver(self):
        self.obs["graph_topics"] = [self.ns + "/my_application/status"]
        self.assertEqual(self.check("ros.driver", mode="preflight")["status"], "pass")
        self.assertEqual(self.check("ros.ur_l.state", mode="preflight")["status"], "not_applicable")

    def test_explicit_observation_domain_does_not_make_session_a_robot_fault(self):
        self.obs.update(domain_id=62, session_domain_id=0, domain_explicit=True)
        result = self.check("ros.domain")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["actual"], 62)
        self.assertIn("Session-Domain: 0", " ".join(result["evidence"]))

    def test_running_driver_wrong_domain_is_independent_failure(self):
        self.obs.update(domain_id=62, session_domain_id=0, domain_explicit=True)
        self.obs["driver_domains"] = {"processes": [{"pid": 42, "namespace": self.ns,
            "basenames": ["bms_can_node.py"], "domain_id": 7}], "unreadable": 0}
        self.assertEqual(self.check("ros.domain")["status"], "pass")
        self.assertEqual(self.check("ros.driver_domain")["status"], "fail")
        self.obs["driver_domains"]["processes"][0]["domain_id"] = 62
        self.assertEqual(self.check("ros.driver_domain")["status"], "pass")

    def test_proc_reader_returns_only_allowed_matching_process_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            def process(pid, executable, namespace, domain):
                path = Path(directory) / str(pid)
                path.mkdir()
                arguments = ["python3", executable, "--ros-args", "-r", "__ns:="+namespace,
                             "unrelated-sensitive-argument"]
                (path / "cmdline").write_bytes("\0".join(arguments).encode())
                environment = "PRIVATE_SECRET=do-not-report\0RMW_IMPLEMENTATION=not-requested\0"
                if domain is not None:
                    environment += "ROS_DOMAIN_ID=" + str(domain) + "\0"
                (path / "environ").write_bytes(environment.encode())
            process(1, "/usr/bin/bms_can_node.py", self.ns, 62)
            process(2, "/usr/bin/ewellix_node", self.ns+"/ewellix_lift_l", 7)
            process(3, "/usr/bin/ewellix_node", "/mur620d/ewellix_lift_l", 42)
            process(4, "/usr/bin/unrelated.py", self.ns, 99)
            process(5, "/usr/bin/mir_bridge", self.ns, None)
            observed = observe_driver_domains(self.ns, directory)
        self.assertEqual({p["pid"] for p in observed["processes"]}, {1, 2, 5})
        self.assertEqual({p["domain_id"] for p in observed["processes"]}, {0, 7, 62})
        serialized = json.dumps(observed)
        for forbidden in ("PRIVATE_SECRET", "do-not-report", "RMW_IMPLEMENTATION", "sensitive-argument"):
            self.assertNotIn(forbidden, serialized)

    def test_context_receives_explicit_domain_without_mutating_environment(self):
        context = SimpleNamespace(ok=lambda: False)
        node = SimpleNamespace(get_node_names_and_namespaces=lambda: [],
                               get_topic_names_and_types=lambda: [], destroy_node=lambda: None)
        executor = SimpleNamespace(add_node=lambda n: None, shutdown=lambda **kw: None,
            spin_once=lambda timeout_sec: time.sleep(min(timeout_sec, 0.001)))
        init = Mock()
        modules = {"rclpy": SimpleNamespace(init=init, create_node=lambda *a, **kw: node),
                   "rclpy.context": SimpleNamespace(Context=lambda: context),
                   "rclpy.executors": SimpleNamespace(SingleThreadedExecutor=lambda **kw: executor)}
        with patch.dict("sys.modules", modules), patch.dict(os.environ, {"ROS_DOMAIN_ID": "7"}):
            result = discover(self.profile, duration=0.001, domain_id=62)
            self.assertEqual(init.call_args.kwargs["domain_id"], 62)
            self.assertEqual(os.environ["ROS_DOMAIN_ID"], "7")
            self.assertEqual(result["domain_id"], 62)
            self.assertEqual(result["session_domain_id"], 7)
            discover(self.profile, duration=0.001)
            self.assertNotIn("domain_id", init.call_args.kwargs)

    def test_lift_key_stats_units_and_current_status(self):
        topic = self.healthy_lift()
        self.obs["topics"][topic]["value"].update(status=[0x31, 0x21, 0],
            speeds=[25, 0], currents=[15, 0, 65535])
        result = self.check("ros.lift_l")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["actual"]["positions"][:2], [322, 323])
        self.assertEqual(result["actual"]["currents_A"], [1.5, 0.0, None])
        self.assertEqual(result["actual"]["speeds"], [25, 0])
        self.assertTrue(result["actual"]["actuator_status"][0]["motion"])
        self.assertTrue(result["actual"]["current_status_confirmed"])
        self.assertIn("bewegt sich", self.evaluate()["stats"]["lift_l"]["text"])

    def test_current_unavailable_actuator_is_not_historical_error(self):
        topic = self.healthy_lift()
        self.obs["topics"][topic]["value"].update(status=[0, 0x21], errors=[0])
        result = self.check("ros.lift_l")
        self.assertEqual(result["status"], "fail")
        self.assertIn("Aktuator 1", result["summary"])
        stat = self.evaluate()["stats"]["lift_l"]
        self.assertTrue(stat["communication_confirmed"])
        self.assertIn("Aktuator nicht verfügbar", stat["text"])
        # Without a verified acquisition these bytes cannot establish a current fault.
        self.obs["lift_diagnostics"].clear()
        result = self.check("ros.lift_l")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["actual"]["status_codes"], [0, 0x21])
        self.assertEqual(result["actual"]["actuator_status"], [])
        self.assertEqual(result["actual"]["current_faults"], [])

    def test_lift_position_limit_bits_do_not_invent_faults(self):
        topic = self.healthy_lift()
        self.obs["topics"][topic]["value"].update(status=[0xC3, 0xC3], errors=[0x400])
        result = self.check("ros.lift_l")
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["actual"]["actuator_status"][0]["out_position"])
        self.assertTrue(result["actual"]["actuator_status"][0]["stroke"])
        self.assertTrue(result["actual"]["actuator_status"][0]["limit_in_out"])
        self.assertEqual(result["actual"]["current_faults"], [])
        self.assertEqual(decode_lift_status([255, -1], True), [])

    def test_watch_uses_persistent_generator(self):
        seen = []
        def fake(*args):
            try:
                seen.append(args)
                yield {"one": 1}
                yield {"two": 2}
            finally:
                seen.append("closed")
        with patch("match_mobile_diagnostics.ros_collector._observe", fake):
            stream = watch(self.profile, duration=10, interval=2)
            self.assertEqual(next(stream), {"one": 1})
            self.assertEqual(next(stream), {"two": 2})
            stream.close()
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0][-2], 2)
        self.assertIsNone(seen[0][-1])
        self.assertEqual(seen[-1], "closed")


if __name__ == "__main__":
    unittest.main()
