"""Bounded, observation-only ROS collector; importable without ROS installed.

Only subscriptions and ListControllers/GetParameters requests are used. Raw
observations can also be evaluated offline with ``evaluate_observations``.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any


ROBOT_MODES = {-1: "NO_CONTROLLER", 0: "DISCONNECTED", 1: "CONFIRM_SAFETY",
               2: "BOOTING", 3: "POWER_OFF", 4: "POWER_ON", 5: "IDLE",
               6: "BACKDRIVE", 7: "RUNNING", 8: "UPDATING_FIRMWARE"}
SAFETY_MODES = {1: "NORMAL", 2: "REDUCED", 3: "PROTECTIVE_STOP", 4: "RECOVERY",
                5: "SAFEGUARD_STOP", 6: "SYSTEM_EMERGENCY_STOP",
                7: "ROBOT_EMERGENCY_STOP", 8: "VIOLATION", 9: "FAULT",
                10: "VALIDATE_JOINT_ID", 11: "UNDEFINED_SAFETY_MODE",
                12: "AUTOMATIC_MODE_SAFEGUARD_STOP",
                13: "SYSTEM_THREE_POSITION_ENABLING_STOP"}
FRESHNESS = {"mir_battery": 10.0, "mur_battery": 5.0, "lift": 2.0, "dashboard": 6.0}
_SUFFIXES = ("/battery_state", "/bms_status/SOC", "/ewellix_lift_l/state",
             "/ewellix_lift_r/state", "/UR10_l/io_and_status_controller/robot_mode",
             "/UR10_r/io_and_status_controller/robot_mode")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def environment_domain() -> int | str:
    """Read, never correct, the caller's effective ROS domain (default is zero)."""
    value = os.environ.get("ROS_DOMAIN_ID", "0")
    try:
        return int(value)
    except ValueError:
        return value


def _observation_domain(domain_id: int | None) -> int | str:
    if domain_id is None:
        return environment_domain()
    if isinstance(domain_id, bool) or not isinstance(domain_id, int) or not 0 <= domain_id <= 232:
        raise ValueError("domain_id muss eine ganze Zahl zwischen 0 und 232 sein")
    return domain_id


_DRIVER_BASENAMES = frozenset({
    "ros2_control_node", "ur_ros2_control_node", "bms_can_node.py", "ewellix_node",
    "mir_bridge", "mir_bridge.py", "mir_driver", "robot_state_publisher",
    "ur_dashboard_client", "dashboard_client", "ur_robot_state_helper",
})


def observe_driver_domains(namespace: str, proc_root: str = "/proc") -> dict:
    """Read only driver identity, ROS_DOMAIN_ID and __ns remaps from local /proc.

    Full command lines and unrelated environment entries are never returned.
    A same-named driver belonging to another robot is excluded.
    """
    selected = normalize_namespace(namespace)
    report = {"processes": [], "unreadable": 0, "source": str(proc_root),
              "observed_at": utc_now(), "received": time.monotonic()}
    try:
        entries = list(Path(proc_root).iterdir())
    except OSError:
        report["unreadable"] = 1
        return report
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            args = [part.decode(errors="replace") for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
            names = sorted({os.path.basename(arg) for arg in args[:3] if not arg.startswith("-")}
                           & _DRIVER_BASENAMES)
            if not names:
                continue
            namespaces = [arg[len("__ns:="):] for arg in args if arg.startswith("__ns:=")
                          and re.fullmatch(r"/[A-Za-z0-9_/]*", arg[len("__ns:="):])]
            if not namespaces:
                continue
            process_namespace = namespaces[-1].rstrip("/") or "/"
            if process_namespace != selected and not process_namespace.startswith(selected + "/"):
                continue
            domain: int | str = 0
            try:
                environment = (entry / "environ").read_bytes()
            except PermissionError:
                report["unreadable"] += 1
                continue
            for field in environment.split(b"\0"):
                key, separator, value = field.partition(b"=")
                if separator and key == b"ROS_DOMAIN_ID":
                    text = value.decode(errors="replace")
                    try:
                        domain = int(text)
                    except ValueError:
                        domain = text
                    break
            report["processes"].append({"pid": int(entry.name), "basenames": names,
                                        "namespace": process_namespace, "domain_id": domain})
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            # Identity is unknown here; do not claim this is a robot process.
            continue
        except OSError:
            continue
    return report


def normalize_namespace(value: str) -> str:
    value = "/" + str(value).strip("/")
    if not re.fullmatch(r"/(?:[A-Za-z_][A-Za-z0-9_]*)(?:/[A-Za-z_][A-Za-z0-9_]*)*", value):
        raise ValueError("Namespace muss ein gültiger, nicht leerer ROS-Namespace sein.")
    return value


def _number(value: Any) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _boolean(value: Any) -> bool | None:
    if value is True or str(value).lower() == "true" or value == 1:
        return True
    if value is False or str(value).lower() == "false" or value == 0:
        return False
    return None


def _age(observation: dict | None, now: float) -> float | None:
    if not observation:
        return None
    received = _number(observation.get("received"))
    if received is None:
        return None
    age = max(0.0, now - received)
    # Header stamps can expose republished old telemetry; mode topics have none.
    header_age = _number(observation.get("header_age_at_receive"))
    if header_age is not None:
        age = max(age, header_age + age)
    return age


def _fresh(observation: dict | None, now: float, limit: float) -> bool:
    age = _age(observation, now)
    return age is not None and age <= limit


def _result(check_id: str, component: str, status: str, summary: str,
            source: str, *, observation: dict | None = None, now: float = 0,
            expected: Any = None, actual: Any = None, evidence: list | None = None,
            causes: list | None = None, next_steps: list | None = None,
            knowledge_id: str | None = None) -> dict:
    return {"id": check_id, "component": component, "status": status,
            "summary": summary, "expected": expected, "actual": actual,
            "source": source,
            "observed_at": (observation or {}).get("observed_at", utc_now()),
            "age_seconds": _age(observation, now), "evidence": evidence or [],
            "causes": causes or [], "next_steps": next_steps or [],
            "knowledge_id": knowledge_id}


def _stat(text: str, source: str = "ROS", observation: dict | None = None,
          now: float = 0, **extra: Any) -> dict:
    return {"text": text, "source": source,
            "observed_at": (observation or {}).get("observed_at"),
            "age_seconds": _age(observation, now), **extra}


def _candidate_namespaces(topic_names: list[str]) -> list[str]:
    found = set()
    for topic in topic_names:
        for suffix in _SUFFIXES:
            if topic.endswith(suffix) and topic != suffix:
                found.add(topic[:-len(suffix)])
    return sorted(found)


def lift_communication_values(namespace: str, side: str, statuses: list[dict]) -> dict | None:
    """Match the exact launch-defined node; never match a global side name."""
    expected = normalize_namespace(namespace) + "/ewellix_lift_" + side + "/ewellix_node/communication"
    matching = [status for status in statuses if status.get("name") == expected]
    if len(matching) != 1:
        return None
    status = matching[0]
    values = dict(status.get("values", {}))
    values["diagnostic_name"] = expected
    values.setdefault("port", status.get("hardware_id", ""))
    return values


def decode_lift_status(codes: list, confirmed: bool) -> list[dict]:
    """Decode only Status1 bits defined by EwellixSerial::Status1::setFromData.

    These are state flags, not a general fault register. In particular,
    out_position, stroke and limit flags must not be treated as error codes.
    0xff is the serial protocol's empty-byte sentinel.
    """
    if not confirmed:
        return []
    names = ("available", "limit_in_out", "switch1", "switch2", "motion",
             "reached", "out_position", "stroke")
    decoded = []
    # The MuR lift driver uses the first two actuator positions for its column.
    for index, code in enumerate(codes[:2]):
        if not isinstance(code, int) or isinstance(code, bool) or not 0 <= code < 255:
            continue
        decoded.append({"actuator": index + 1, "code": code,
                        **{name: bool(code & (1 << bit)) for bit, name in enumerate(names)}})
    return decoded


def evaluate_observations(profile: dict, observations: dict,
                          namespace: str | None = None,
                          mode: str = "operational", now: float | None = None) -> dict:
    """Evaluate plain observations, with monotonic receipt times, without ROS.

    ``topics`` maps fully-qualified topics to {value, received, observed_at};
    ``publishers`` maps them to endpoint counts. ``controllers`` maps l/r to
    observations whose values are lists of {name,state,claimed_interfaces}.
    ``lift_diagnostics`` maps l/r to observations of the communication keys.
    ``nodes`` contains fully-qualified names (duplicates deliberately retained).
    """
    if mode not in ("operational", "preflight"):
        raise ValueError("mode muss operational oder preflight sein")
    now = time.monotonic() if now is None else now
    robot = str(profile.get("robot") or profile.get("id") or profile.get("name") or "mur620")
    selected = normalize_namespace(namespace or robot)
    limits = {**FRESHNESS, **profile.get("freshness", {})}
    limits = {k: max(0.1, _number(v) or FRESHNESS.get(k, 5.0)) for k, v in limits.items()}
    actual_domain = observations.get("domain_id", environment_domain())
    expected_domain = profile.get("ros_domain_id", 62)
    session_domain = observations.get("session_domain_id", environment_domain())
    results: list[dict] = [_result("ros.domain", "ros",
        "pass" if actual_domain == expected_domain else "fail",
        "Beobachtungsdomain stimmt mit dem Profil überein." if actual_domain == expected_domain else
        "Beobachtungsdomain weicht vom Roboterprofil ab.", "collector observation domain",
        expected=expected_domain, actual=actual_domain, knowledge_id="ros_discovery",
        evidence=[f"Ursprüngliche Session-Domain: {session_domain}",
                  "Explizite Beobachtungsdomain; die Session-Umgebung bleibt unverändert."
                  if observations.get("domain_explicit") else "Beobachtungsdomain aus der Session-Umgebung."],
        next_steps=[] if actual_domain == expected_domain else
        [f"Beobachtungsdomain explizit mit --domain-id {expected_domain} wählen; Treiberdomain separat prüfen."])]
    stats = {key: _stat("Unbekannt") for key in
             ("mir_battery", "mur_battery", "ur_l", "ur_r", "lift_l", "lift_r")}
    topics = observations.get("topics", {})
    graph_topics = observations.get("graph_topics", list(topics))
    candidates = _candidate_namespaces(graph_topics)
    nodes = observations.get("nodes", [])
    relevant_nodes = [n for n in nodes if n.startswith(selected + "/")]
    relevant_topics = [t for t in graph_topics if t.startswith(selected + "/")]
    if not relevant_nodes and not relevant_topics and candidates and namespace is None:
        results.append(_result("ros.namespace", "ros", "unknown",
            "ROS-Namespace ist dem ausgewählten Roboter nicht eindeutig zugeordnet.",
            "ROS graph", expected=selected, actual=candidates,
            next_steps=["Roboterzuordnung prüfen und den ROS-Namespace explizit auswählen."],
            knowledge_id="ros_discovery"))
    else:
        results.append(_result("ros.namespace", "ros", "pass",
            "Expliziter ROS-Namespace." if namespace else "Roboterprofil bestimmt den ROS-Namespace.",
            "profile/ROS graph", expected=normalize_namespace(robot), actual=selected))
    seen_driver = any(re.search(r"/(UR10_[lr]|ewellix_lift_[lr])(?:/|$)|/bms_can_node$|/mir_bridge$", n)
                      for n in relevant_nodes) or selected in _candidate_namespaces(relevant_topics)
    results.append(_result("ros.driver", "ros",
        "pass" if seen_driver or mode == "preflight" else "fail",
        "ROS-Treibergraph sichtbar." if seen_driver else
        "Treiber noch nicht sichtbar; vor dem Start ist das erwartbar." if mode == "preflight" else
        "Kein Treiber im gewählten ROS-Namespace sichtbar.",
        "ROS graph", expected=selected, actual=relevant_nodes,
        next_steps=[] if seen_driver else ["Treiberstart, ROS_DOMAIN_ID, Discovery und Namespace prüfen."],
        knowledge_id="ros_discovery"))
    driver_observation = observations.get("driver_domains", {})
    driver_processes = driver_observation.get("processes", [])
    wrong_domains = [process for process in driver_processes if process.get("domain_id") != expected_domain]
    unreadable = driver_observation.get("unreadable", 0)
    if wrong_domains:
        driver_domain_status = "fail"
        driver_domain_summary = "Laufende lokale Treiber verwenden eine vom Profil abweichende ROS-Domain."
    elif unreadable:
        driver_domain_status = "unknown"
        driver_domain_summary = "ROS-Domain mindestens eines lokalen Treibers nicht lesbar."
    elif driver_processes:
        driver_domain_status = "pass"
        driver_domain_summary = "Laufende lokale Treiber verwenden die erwartete ROS-Domain."
    else:
        driver_domain_status = "not_applicable" if mode == "preflight" and not seen_driver else "unknown"
        driver_domain_summary = "Keine passenden lokalen Treiberprozesse für diese Namespace-Zuordnung beobachtet."
    results.append(_result("ros.driver_domain", "ros", driver_domain_status, driver_domain_summary,
        "local /proc/<pid>/{cmdline,environ}; selected driver basenames, __ns and ROS_DOMAIN_ID only",
        observation=driver_observation, now=now, expected=expected_domain,
        actual=driver_processes, knowledge_id="ros_discovery",
        evidence=[f"Nicht lesbare passende Prozesse: {unreadable}"] if unreadable else [],
        next_steps=["ROS_DOMAIN_ID des betreffenden Treiberstarts mit dem Roboterprofil abgleichen."]
        if wrong_domains else []))
    duplicates = sorted(n for n, count in Counter(relevant_nodes).items() if count > 1)
    results.append(_result("ros.duplicate_nodes", "ros", "warn" if duplicates else "pass",
        "Mehrfach vorhandene ROS-Node-Namen." if duplicates else "Keine doppelten Node-Namen beobachtet.",
        "ROS graph", actual=duplicates, knowledge_id="driver_installation"))

    for kind, suffix, factor in (("mir_battery", "/battery_state", 100.0),
                                 ("mur_battery", "/bms_status/SOC", 1.0)):
        topic = selected + suffix
        sample = topics.get(topic)
        raw = _number((sample or {}).get("value"))
        value = None if raw is None else raw * factor
        age = _age(sample, now)
        status, summary = "pass", "Aktueller Akkuladestand empfangen."
        if not sample:
            status, summary = "unknown", "Keine Batterietelemetrie empfangen."
        elif value is None or not 0 <= value <= 100:
            status, summary = "unknown", "Ungültiger Akkuladestand; keine Prozentanzeige möglich."
        elif sample.get("clock_invalid"):
            status, summary = "unknown", "Batterie-Zeitstempel liegt in der Zukunft; Uhrzeit prüfen."
        elif not _fresh(sample, now, limits[kind]):
            status, summary = "unknown", "Batterietelemetrie ist veraltet."
        elif value <= 10:
            status, summary = "warn", "Akkuladestand niedrig (höchstens 10 %)."
        results.append(_result(f"ros.{kind}", kind, status, summary, topic,
            observation=sample, now=now, expected=f"0–100 %, Alter ≤ {limits[kind]:g} s",
            actual={"percent": value, "age_seconds": age}, knowledge_id="battery_can" if kind == "mur_battery" else None,
            next_steps=["ROS-Topic, Treiber und Verbindung prüfen."] if status == "unknown" else []))
        valid = value is not None and 0 <= value <= 100 and status != "unknown"
        stats[kind] = _stat(f"{value:.1f} %" if valid else "Unbekannt", topic, sample, now,
                            percent=value if valid else None, status=status)

    param = observations.get("bms_id")
    expected_id = profile.get("bms_id")
    actual_id = (param or {}).get("value")
    def parse_id(value: Any) -> int | None:
        try:
            return int(value, 0) if isinstance(value, str) else int(value)
        except (TypeError, ValueError):
            return None
    if expected_id is not None:
        parsed = parse_id(actual_id)
        status = "unknown" if parsed is None or not _fresh(param, now, 10.0) else ("pass" if parsed == parse_id(expected_id) else "fail")
        results.append(_result("ros.bms_id", "mur_battery", status,
            "BMS-Kennung stimmt mit dem Profil überein." if status == "pass" else
            ("BMS-Kennung weicht vom Roboterprofil ab." if status == "fail" else "Laufende BMS-Kennung nicht lesbar."),
            selected + "/bms_can_node/get_parameters", observation=param, now=now,
            expected=expected_id, actual=actual_id, knowledge_id="battery_can"))

    for side in ("l", "r"):
        component = "ur_" + side
        prefix = selected + "/UR10_" + side + "/io_and_status_controller/"
        controller_sample = observations.get("controllers", {}).get(side)
        controllers = (controller_sample or {}).get("value", [])
        active = [c.get("name") for c in controllers if c.get("state") == "active"]
        motion = [c.get("name") for c in controllers
                  if c.get("state") == "active" and c.get("claimed_interfaces")]
        controller_fresh = _fresh(controller_sample, now, max(limits["dashboard"], 10.0))
        results.append(_result(f"ros.{component}.controllers", component,
            ("pass" if motion else ("warn" if mode == "preflight" else "fail")) if controller_fresh else "unknown",
            "Aktive Bewegungscontroller vorhanden." if controller_fresh and motion else
            ("Kein aktiver Bewegungscontroller." if controller_fresh else "Controller-Manager nicht aktuell abfragbar."),
            selected + "/UR10_" + side + "/controller_manager/list_controllers",
            observation=controller_sample, now=now, actual=controllers,
            knowledge_id="ur_connection"))
        samples = {key: topics.get(prefix + key) for key in
                   ("robot_mode", "safety_mode", "robot_program_running")}
        io = topics.get(prefix + "io_states")
        # A latched change-only mode is not stale merely because it is unchanged.
        # Fresh I/O, publishers and an answering manager corroborate it.
        status_publishers = all(observations.get("publishers", {}).get(prefix + key, 0) > 0
                                for key in samples)
        live = (_fresh(io, now, limits["dashboard"]) and controller_fresh and status_publishers)
        robot_value = (samples["robot_mode"] or {}).get("value")
        safety_value = (samples["safety_mode"] or {}).get("value")
        program_value = (samples["robot_program_running"] or {}).get("value")
        robot_name = ROBOT_MODES.get(robot_value, "UNKNOWN")
        safety_name = SAFETY_MODES.get(safety_value, "UNKNOWN")
        complete = robot_value in ROBOT_MODES and safety_value in SAFETY_MODES and isinstance(program_value, bool)
        if not live or not complete:
            status, summary = "unknown", "UR-Status nicht durch aktuelle Treiberkommunikation bestätigt."
        elif safety_value not in (1, 2):
            status, summary = "fail", f"UR-Sicherheitszustand: {safety_name}."
        elif mode == "operational" and (robot_value != 7 or program_value is not True):
            status, summary = "fail", f"UR nicht betriebsbereit: {robot_name}, Programm läuft={program_value}."
        elif mode == "preflight" and robot_value == 3:
            status, summary = "pass", "UR ausgeschaltet; POWER_OFF ist vor dem Start zulässig."
        elif robot_value in (-1, 0, 1, 2, 8):
            status, summary = "warn", f"UR-Zustand {robot_name} erfordert weitere Prüfung."
        else:
            status, summary = "pass", f"UR: {robot_name}, Sicherheit {safety_name}."
        results.append(_result(f"ros.{component}.state", component, status, summary, prefix,
            observation=io, now=now,
            expected="Aktuelle Treiberkommunikation und bestätigter UR-Zustand",
            actual={"robot_mode": robot_name, "safety_mode": safety_name,
                    "program_running": program_value, "liveness_confirmed": live},
            evidence=["Modus-Topics senden nur bei Änderungen; Alter allein ist kein Ausfallnachweis.",
                      "ROS-Status ersetzt keine separate Prüfung der UR-Rückverbindung."],
            knowledge_id="ur_connection"))
        stats[component] = _stat(
            ((f"{robot_name} / {safety_name}" if live and complete else "Unbestätigt")
             + (" | " + ", ".join(motion) if controller_fresh and motion else
                " | kein aktiver Bewegungscontroller" if controller_fresh else " | Controller unbekannt")), prefix, io, now,
            status=status, robot_mode=robot_name, safety_mode=safety_name,
            program_running=program_value, controllers=active if controller_fresh else [],
            motion_controllers=motion if controller_fresh else [], controllers_available=controller_fresh, liveness_confirmed=live)

    for side in ("l", "r"):
        component = "lift_" + side
        prefix = selected + "/ewellix_lift_" + side
        if not profile.get("has_lifts", False):
            results.append(_result(f"ros.{component}", component, "not_applicable",
                                   "Dieses Roboterprofil hat keine Hubsäulen.", "profile"))
            stats[component] = _stat("Nicht vorhanden", "profile", status="not_applicable")
            continue
        state = topics.get(prefix + "/state")
        value = (state or {}).get("value", {})
        positions = [_number(p) for p in value.get("actual_positions", [])[:2]]
        positions = [p for p in positions if p is not None and p >= 0]
        scale = _number(profile.get("lift_ticks_per_meter", 3225.0))
        height = sum(positions) / len(positions) / scale if positions and scale and scale > 0 else None
        diag = observations.get("lift_diagnostics", {}).get(side)
        d = (diag or {}).get("value", {})
        comm = _boolean(d.get("hardware_comm_ok"))
        cycle = _boolean(d.get("last_cycle_ok"))
        success_age = _number(d.get("last_success_age_sec"))
        count = _number(d.get("successful_cycles"))
        failures = _number(d.get("consecutive_failures"))
        diag_age = _age(diag, now)
        effective_success_age = None if success_age is None else success_age + (diag_age or 0)
        history = [int(code) for code in value.get("errors", []) if isinstance(code, int) and code not in (0, 0xFFFFFFFF)]
        diag_fresh = _fresh(diag, now, limits["lift"])
        if not _fresh(state, now, limits["lift"]) or height is None:
            status, summary = "unknown", "Keine aktuelle, gültige Lift-Position."
        elif not diag_fresh:
            status, summary = "unknown", "Lift-State vorhanden; Hardwarekommunikation ohne aktuelle Diagnose unbestätigt."
        elif comm is False or cycle is False or count == 0 or (failures is not None and failures > 0):
            status, summary = "fail", "Hubsäulen-Treiber meldet einen Kommunikationsfehler."
        elif effective_success_age is not None and effective_success_age > limits["lift"]:
            status, summary = "fail", "Letzter erfolgreicher Lift-Hardwarezyklus liegt zu lange zurück."
        elif comm is True and cycle is True and count is not None and count > 0 and effective_success_age is not None and effective_success_age >= 0:
            status, summary = "pass", "Lift-Position und Hardwarekommunikation aktuell."
        else:
            status, summary = "unknown", "Lift-Kommunikationsdiagnose unvollständig."
        communication_confirmed = status == "pass"
        status_codes = list(value.get("status", []))
        decoded_status = decode_lift_status(status_codes, communication_confirmed)
        unavailable = [item["actuator"] for item in decoded_status if not item["available"]]
        current_faults = [f"Aktuator {index} meldet available=false." for index in unavailable]
        if current_faults:
            status = "fail"
            summary = "Aktuelle Hubsäulenmeldung: Aktuator " + ", ".join(map(str, unavailable)) + " nicht verfügbar."
        raw_positions = [_number(position) for position in value.get("actual_positions", [])]
        speeds = [_number(speed) for speed in value.get("speeds", [])]
        currents_a = []
        for raw_current in value.get("currents", []):
            current = _number(raw_current)
            currents_a.append(current / 10.0 if current is not None and 0 <= current < 65535 else None)
        results.append(_result(f"ros.{component}", component, status, summary, prefix + "/state + /diagnostics",
            observation=state, now=now, expected=f"Erfolgreicher Hardwarezyklus innerhalb {limits['lift']:g} s",
            actual={"height_m": height, "positions": raw_positions, "position_unit": "ticks",
                    "positions_m": [position / scale if position is not None and position >= 0 and scale and scale > 0 else None
                                    for position in raw_positions],
                    "speeds": speeds, "speed_unit": "percent", "currents_A": currents_a,
                    "status_codes": status_codes, "actuator_status": decoded_status,
                    "current_status_confirmed": communication_confirmed and len(decoded_status) == 2,
                    "current_faults": current_faults,
                    "hardware_comm_ok": comm, "last_cycle_ok": cycle,
                    "last_success_age_sec": effective_success_age, "successful_cycles": count,
                    "consecutive_failures": failures, "failure_reason": d.get("failure_reason"),
                    "port": d.get("port"), "error_history": history},
            evidence=(["State.errors ist die Fehlerhistorie, kein Nachweis eines aktuellen Fehlers."] if history else []) +
                     (["Status1-Bits gemäß EwellixSerial::Status1::setFromData; Limit-/Positionsbits sind keine allgemeinen Fehlerbits."]
                      if decoded_status else []),
            knowledge_id="lift_serial"))
        height_text = f"{height:.3f} m" if height is not None else "Unbekannt"
        if height is not None and status != "pass":
            height_text += " (Aktuator nicht verfügbar)" if current_faults else (
                " (Kommunikationsfehler)" if status == "fail" else " (unbestätigt)")
        elif decoded_status:
            height_text += " | bewegt sich" if any(item["motion"] for item in decoded_status) else " | Stillstand"
        stats[component] = _stat(height_text, prefix, state, now,
                                height_m=height, status=status, communication_confirmed=communication_confirmed,
                                positions=raw_positions, speeds=speeds, currents_A=currents_a,
                                status_codes=status_codes, actuator_status=decoded_status,
                                current_faults=current_faults,
                                current_status_confirmed=communication_confirmed and len(decoded_status) == 2,
                                error_history=history)
    if mode == "preflight" and not seen_driver:
        for result in results:
            if result["component"] in stats:
                result.update(status="not_applicable",
                    summary="Laufzeitprüfung entfällt vor dem Treiberstart.", next_steps=[])
        for component in stats:
            if profile.get("has_lifts") or not component.startswith("lift_"):
                stats[component] = _stat("Vor Treiberstart nicht verfügbar", "ROS graph", status="not_applicable")
    for issue in observations.get("errors", []):
        results.append(_result("ros.collector." + str(len(results)), "ros", "unknown", str(issue), "ROS collector"))
    return {"namespace": selected, "domain_id": actual_domain, "session_domain_id": session_domain,
            "domain_explicit": bool(observations.get("domain_explicit")),
            "results": results, "stats": stats}


def _snapshot_value(message: Any, kind: str) -> Any:
    if kind == "battery":
        return float(message.percentage)
    if kind == "float":
        return float(message.data)
    if kind == "bool":
        return bool(message.data)
    if kind == "mode":
        return int(message.mode)
    if kind == "lift":
        return {field: list(getattr(message, field)) for field in
                ("actual_positions", "remote_positions", "speeds", "currents", "status", "errors")}
    return True


def _observe(profile: dict, namespace: str | None = None,
             duration: float = 10.0, mode: str = "operational",
             interval: float | None = None, domain_id: int | None = None):
    """Observe one ROS domain for at most ``duration`` seconds (0 < N <= 60)."""
    if mode not in ("operational", "preflight"):
        raise ValueError("mode muss operational oder preflight sein")
    duration = float(duration)
    if not math.isfinite(duration) or not 0 < duration <= 60:
        raise ValueError("duration muss zwischen 0 und 60 Sekunden liegen")
    robot = profile.get("robot") or profile.get("id") or profile.get("name") or "mur620"
    selected = normalize_namespace(namespace or robot)
    observed_domain = _observation_domain(domain_id)
    obs: dict = {"topics": {}, "publishers": {}, "nodes": [], "graph_topics": [],
                 "controllers": {}, "lift_diagnostics": {}, "errors": [], "domain_id": observed_domain,
                 "session_domain_id": environment_domain(), "domain_explicit": domain_id is not None,
                 "driver_domains": observe_driver_domains(selected)}
    try:
        import rclpy
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
    except ImportError as exc:
        result = evaluate_observations(profile, obs, namespace, mode)
        domain_results = [r for r in result["results"] if r["id"] in ("ros.domain", "ros.driver_domain")]
        result["results"] = [_result("ros.available", "ros", "unknown",
            "ROS-Python-Umgebung nicht verfügbar; ROS-Prüfungen wurden nicht ausgeführt.",
            "Python import", actual=str(exc), next_steps=["ROS Jazzy und Workspace in der Collector-Umgebung sourcen."])] + domain_results
        yield result
        return

    context = Context()
    node = executor = None
    started = time.monotonic()
    deadline = started + duration
    pending: dict[str, tuple] = {}
    last_request: dict[str, float] = {}
    clients = {}
    subscriptions = []
    missing_types = set()

    def message_type(module: str, name: str):
        try:
            return getattr(importlib.import_module(module), name)
        except (ImportError, AttributeError) as exc:
            key = module + "." + name
            if key not in missing_types:
                obs["errors"].append(f"Optionaler ROS-Typ {key} fehlt: {exc}")
                missing_types.add(key)
            return None

    def record(value: Any, message: Any = None) -> dict:
        item = {"value": value, "received": time.monotonic(), "observed_at": utc_now()}
        stamp = getattr(getattr(message, "header", None), "stamp", None)
        if stamp is not None:
            seconds = stamp.sec + stamp.nanosec * 1e-9
            if seconds > 0:
                age = time.time() - seconds
                item["clock_invalid"] = age < -5
                item["header_age_at_receive"] = max(0.0, age)
        return item

    def subscribe(topic: str, cls: Any, kind: str, latched: bool = False):
        if cls is None:
            return
        qos = QoSProfile(depth=10,
            reliability=ReliabilityPolicy.RELIABLE if latched else ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.TRANSIENT_LOCAL if latched else DurabilityPolicy.VOLATILE)
        def callback(message):
            try:
                obs["topics"][topic] = record(_snapshot_value(message, kind), message)
            except Exception as exc:
                error = f"Ungültige Nachricht auf {topic}: {exc}"
                if error not in obs["errors"]:
                    obs["errors"].append(error)
        subscriptions.append(node.create_subscription(cls, topic, callback, qos))

    def diag_callback(side: str, message):
        statuses = [{"name": status.name, "hardware_id": status.hardware_id,
                     "values": {pair.key: pair.value for pair in status.values}}
                    for status in message.status]
        values = lift_communication_values(selected, side, statuses)
        if values is not None:
            obs["lift_diagnostics"][side] = record(values, message)

    try:
        init_options = {"domain_id": observed_domain} if domain_id is not None else {}
        rclpy.init(args=[], context=context, **init_options)
        node = rclpy.create_node("match_mobile_diagnostics_" + str(os.getpid()),
            context=context, enable_rosout=False, start_parameter_services=False,
            use_global_arguments=False)
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(node)
        subscribe(selected + "/battery_state", message_type("sensor_msgs.msg", "BatteryState"), "battery")
        subscribe(selected + "/bms_status/SOC", message_type("std_msgs.msg", "Float32"), "float")
        mode_cls = message_type("ur_dashboard_msgs.msg", "RobotMode")
        safety_cls = message_type("ur_dashboard_msgs.msg", "SafetyMode")
        bool_cls = message_type("std_msgs.msg", "Bool")
        io_cls = message_type("ur_msgs.msg", "IOStates")
        list_cls = message_type("controller_manager_msgs.srv", "ListControllers")
        get_cls = message_type("rcl_interfaces.srv", "GetParameters")
        for side in ("l", "r"):
            prefix = selected + "/UR10_" + side
            for suffix, cls, kind, latched in (("robot_mode", mode_cls, "mode", True),
                    ("safety_mode", safety_cls, "mode", True),
                    ("robot_program_running", bool_cls, "bool", True),
                    ("io_states", io_cls, "io", False)):
                subscribe(prefix + "/io_and_status_controller/" + suffix, cls, kind, latched)
            if list_cls:
                clients["controller_" + side] = (node.create_client(list_cls,
                    prefix + "/controller_manager/list_controllers"), list_cls, side)
            if profile.get("has_lifts", False):
                lift_prefix = selected + "/ewellix_lift_" + side
                subscribe(lift_prefix + "/state", message_type("ewellix_interfaces.msg", "State"), "lift")
                diag_cls = message_type("diagnostic_msgs.msg", "DiagnosticArray")
                if diag_cls:
                    qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)
                    subscriptions.append(node.create_subscription(diag_cls, lift_prefix + "/diagnostics",
                        lambda msg, side=side: diag_callback(side, msg), qos))
        if get_cls:
            clients["bms_id"] = (node.create_client(get_cls, selected + "/bms_can_node/get_parameters"), get_cls, None)
        next_graph = 0.0
        next_process_check = time.monotonic() + 2.0
        while time.monotonic() < deadline or interval is not None:
            current = time.monotonic()
            if current >= next_process_check:
                obs["driver_domains"] = observe_driver_domains(selected)
                next_process_check = current + 2.0
            if current >= next_graph:
                obs["nodes"] = [(ns.rstrip("/") + "/" + name) for name, ns in node.get_node_names_and_namespaces()
                                if not name.startswith("match_mobile_diagnostics_")]
                obs["graph_topics"] = [name for name, _ in node.get_topic_names_and_types()
                                       if node.count_publishers(name) > 0]
                for side in ("l", "r"):
                    for suffix in ("robot_mode", "safety_mode", "robot_program_running"):
                        topic = selected + "/UR10_" + side + "/io_and_status_controller/" + suffix
                        obs["publishers"][topic] = node.count_publishers(topic)
                next_graph = current + 0.5
            for key, (client, cls, side) in clients.items():
                if key in pending:
                    future, request_time = pending[key]
                    if future.done():
                        try:
                            response = future.result()
                            if key == "bms_id":
                                if response.values:
                                    parameter = response.values[0]
                                    value = parameter.integer_value if parameter.type == 2 else parameter.string_value if parameter.type == 4 else None
                                    obs["bms_id"] = record(value)
                            else:
                                obs["controllers"][side] = record([{"name": c.name, "state": c.state,
                                    "claimed_interfaces": list(c.claimed_interfaces)} for c in response.controller])
                        except Exception as exc:
                            obs["errors"].append(f"ROS-Abfrage {key} fehlgeschlagen: {exc}")
                        del pending[key]
                    elif current - request_time >= 2.0:
                        client.remove_pending_request(future)
                        future.cancel()
                        del pending[key]
                if key not in pending and current - last_request.get(key, -10) >= 2.0 and client.service_is_ready():
                    request = cls.Request()
                    if key == "bms_id":
                        request.names = ["battery_node_id"]
                    pending[key] = (client.call_async(request), current)
                    last_request[key] = current
            executor.spin_once(timeout_sec=min(0.1, max(0.0, deadline - time.monotonic())))
            if interval is not None and time.monotonic() >= deadline:
                yield evaluate_observations(profile, obs, namespace, mode)
                deadline = time.monotonic() + interval
    except Exception as exc:
        obs["errors"].append(f"ROS-Erfassung fehlgeschlagen: {type(exc).__name__}: {exc}")
    finally:
        for future, _ in pending.values():
            future.cancel()
        if executor is not None:
            executor.shutdown(timeout_sec=0.1)
        if node is not None:
            node.destroy_node()
        if context.ok():
            context.shutdown()
    yield evaluate_observations(profile, obs, namespace, mode)


def collect(profile: dict, namespace: str | None = None,
            duration: float = 10.0, mode: str = "operational",
            domain_id: int | None = None) -> dict:
    """Collect one snapshot and release all ROS resources before returning."""
    stream = _observe(profile, namespace, duration, mode, domain_id=domain_id)
    try:
        return next(stream)
    finally:
        stream.close()


def watch(profile: dict, namespace: str | None = None, duration: float = 10.0,
          mode: str = "operational", interval: float = 2.0,
          domain_id: int | None = None):
    """Yield snapshots from one persistent ROS node after the acquisition grace.

    Closing the iterator releases the executor, subscriptions and private context.
    Samples remain cached and expire according to their per-signal freshness.
    """
    interval = float(interval)
    if not math.isfinite(interval) or not 0.1 <= interval <= 60:
        raise ValueError("interval muss zwischen 0.1 und 60 Sekunden liegen")
    yield from _observe(profile, namespace, duration, mode, interval, domain_id)


def discover(profile: dict, namespace: str | None = None, duration: float = 3.0,
             domain_id: int | None = None) -> dict:
    """Observe only the ROS graph in the actual session domain, without services."""
    duration = float(duration)
    if not math.isfinite(duration) or not 0 < duration <= 60:
        raise ValueError("duration muss zwischen 0 und 60 Sekunden liegen")
    selected = normalize_namespace(namespace or profile.get("robot") or profile.get("id") or "mur620")
    observed_domain = _observation_domain(domain_id)
    result = {"namespace": selected, "visible": False, "domain_id": observed_domain,
              "session_domain_id": environment_domain(), "domain_explicit": domain_id is not None,
              "expected_domain_id": profile.get("ros_domain_id", 62), "available": False,
              "nodes": [], "topics": [], "observed_at": utc_now()}
    try:
        import rclpy
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor
    except ImportError as exc:
        result["error"] = str(exc)
        return result
    context = Context()
    node = executor = None
    deadline = time.monotonic() + duration
    try:
        init_options = {"domain_id": observed_domain} if domain_id is not None else {}
        rclpy.init(args=[], context=context, **init_options)
        node = rclpy.create_node("match_mobile_diagnostics_discovery_" + str(os.getpid()),
            context=context, enable_rosout=False, start_parameter_services=False,
            use_global_arguments=False)
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(node)
        while time.monotonic() < deadline:
            executor.spin_once(timeout_sec=min(0.1, max(0.0, deadline-time.monotonic())))
        result["nodes"] = [ns.rstrip("/") + "/" + name for name, ns in node.get_node_names_and_namespaces()
                           if not name.startswith("match_mobile_diagnostics_")]
        result["topics"] = [name for name, _ in node.get_topic_names_and_types() if node.count_publishers(name) > 0]
        result["visible"] = any(name.startswith(selected + "/") for name in result["nodes"] + result["topics"])
        result["available"] = True
        result["observed_at"] = utc_now()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if executor is not None:
            executor.shutdown(timeout_sec=0.1)
        if node is not None:
            node.destroy_node()
        if context.ok():
            context.shutdown()
    return result


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--mode", choices=("operational", "preflight"), default="operational")
    parser.add_argument("--namespace")
    parser.add_argument("--domain-id", type=int, help="Explizite Beobachtungsdomain; Session-Domain wird separat berichtet")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--discovery-only", action="store_true")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args(argv)
    try:
        profile = json.load(sys.stdin)
        if not isinstance(profile, dict):
            raise ValueError("Profil muss ein JSON-Objekt sein")
        if args.discovery_only:
            if args.watch:
                raise ValueError("--watch und --discovery-only sind nicht kombinierbar")
            result = discover(profile, args.namespace, args.duration, args.domain_id)
            print(json.dumps(_json_safe(result), ensure_ascii=False, allow_nan=False))
        elif args.watch:
            stream = watch(profile, args.namespace, args.duration, args.mode, args.interval, args.domain_id)
            try:
                for result in stream:
                    print(json.dumps(_json_safe(result), ensure_ascii=False, allow_nan=False), flush=True)
            finally:
                stream.close()
        else:
            result = collect(profile, args.namespace, args.duration, args.mode, args.domain_id)
            print(json.dumps(_json_safe(result), ensure_ascii=False, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
