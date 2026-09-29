"""UR Dashboard observation. Only these literal query commands can be sent."""
import socket
import time
from .models import result, utc_now

QUERIES = ("PolyscopeVersion", "robotmode", "safetystatus", "safetymode",
           "programState", "is in remote control", "get loaded program")
BLOCKING = {"PROTECTIVE_STOP", "ROBOT_EMERGENCY_STOP", "SYSTEM_EMERGENCY_STOP",
            "SAFEGUARD_STOP", "AUTOMATIC_MODE_SAFEGUARD_STOP", "SYSTEM_THREE_POSITION_ENABLING_STOP",
            "FAULT", "VIOLATION", "RECOVERY"}


ROBOT_MODES = {"NO_CONTROLLER", "DISCONNECTED", "CONFIRM_SAFETY", "BOOTING", "POWER_OFF",
               "POWER_ON", "IDLE", "BACKDRIVE", "RUNNING", "UPDATING_FIRMWARE"}
PREFLIGHT_MODES = {"POWER_OFF", "IDLE", "RUNNING"}
PROGRAM_STATES = {"PLAYING", "PAUSED", "STOPPED"}

def supported(answer):
    return bool(answer) and not any(token in answer.lower() for token in
        ("not found", "unknown command", "not supported", "unsupported", "could not understand"))


def query_dashboard(host, timeout=2.0, port=29999):
    observations = {"reachable": False, "answers": {}, "error": "", "observed_at": utc_now()}
    deadline = time.monotonic() + 12.0
    buffer = b""
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            def readline():
                nonlocal buffer
                while b"\n" not in buffer:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Zeitlimit der Dashboard-Abfrage erreicht")
                    connection.settimeout(min(timeout, remaining))
                    chunk = connection.recv(512)
                    if not chunk:
                        raise ConnectionError("Dashboard-Verbindung geschlossen")
                    buffer += chunk
                    if len(buffer) > 8192:
                        raise ValueError("Dashboard-Antwort zu lang")
                line, buffer = buffer.split(b"\n", 1)
                return line.decode("ascii", errors="replace").strip()
            observations["banner"] = readline()
            observations["reachable"] = True
            for command in QUERIES:
                connection.sendall((command + "\n").encode("ascii"))
                observations["answers"][command] = readline()
    except (OSError, ValueError) as exc:
        observations["error"] = str(exc)
    observations["_received_monotonic"] = time.monotonic()
    return observations


def evaluate_dashboard(side, profile, observation, mode="operational"):
    component = "UR " + ("links" if side == "l" else "rechts")
    prefix = f"ur.{side}"
    source = f"{profile['arms'][side]['address']}:29999"
    answers = observation.get("answers", {})
    age = max(0.0, time.monotonic() - observation.get("_received_monotonic", time.monotonic()))
    common = dict(source=source, observed_at=observation.get("observed_at"), age_seconds=age, knowledge_id="ur_connection")
    results = [result(prefix + ".dashboard", component,
                      "pass" if observation.get("reachable") and not observation.get("error") else "fail",
                      "Dashboard beantwortet Statusabfragen" if observation.get("reachable") and not observation.get("error")
                      else "Dashboard nicht vollständig erreichbar", expected="Lesbare Dashboard-Antworten",
                      actual=observation.get("error") or observation.get("banner"),
                      next_steps=[] if not observation.get("error") else ["Versorgung, Netzwerkpfad und Dashboard am Teach Pendant prüfen."], **common)]
    raw_safety = answers.get("safetystatus", "")
    if not supported(raw_safety):
        raw_safety = answers.get("safetymode", "")
    safety = raw_safety.split(":", 1)[-1].strip().upper() if supported(raw_safety) else ""
    if safety in BLOCKING or safety.endswith("SAFEGUARD_STOP"):
        safety_status, safety_summary = "fail", "UR meldet Sicherheitsstopp: " + safety
    elif safety in ("NORMAL", "REDUCED"):
        safety_status, safety_summary = "pass", "UR-Sicherheitszustand: " + safety
    else:
        safety_status, safety_summary = "unknown", "UR-Sicherheitszustand nicht prüfbar"
    results.append(result(prefix + ".safety", component, safety_status, safety_summary,
                          expected="NORMAL oder REDUCED", actual=raw_safety or None,
                          next_steps=["Ursache am Teach Pendant und im Arbeitsbereich prüfen; erst anschließend nach Betriebsanweisung beheben."] if safety_status == "fail" else [], **common))
    remote = answers.get("is in remote control", "")
    remote_value = remote.split(":")[-1].strip().lower()
    if remote_value in ("true", "false"):
        remote_status = "pass" if remote_value == "true" else "fail"
        remote_summary = "Remote Control aktiv" if remote_value == "true" else "Remote Control nicht aktiv"
    elif remote and not supported(remote):
        remote_status, remote_summary = "not_applicable", "Remote-Control-Abfrage von dieser Software nicht unterstützt"
    else:
        remote_status, remote_summary = "unknown", "Remote-Control-Modus nicht prüfbar"
    results.append(result(prefix + ".remote", component, remote_status, remote_summary,
                          actual=remote or None, expected="Remote, wenn unterstützt", **common))
    robot_mode = answers.get("robotmode", "").split(":")[-1].strip().upper()
    program = answers.get("programState", "")
    program_state = program.strip().split()[0].upper() if program.strip() else ""
    program_known = supported(program) and program_state in PROGRAM_STATES
    program_running = program_state == "PLAYING"
    state_known = supported(answers.get("robotmode", "")) and robot_mode in ROBOT_MODES
    mode_ready = robot_mode in PREFLIGHT_MODES if mode == "preflight" else robot_mode == "RUNNING"
    status = "unknown" if not state_known else ("pass" if mode_ready else "fail")
    results.append(result(prefix + ".mode", component, status,
                          "UR-Betriebszustand: " + (robot_mode or "unbekannt"),
                          expected="RUNNING" if mode == "operational" else "POWER_OFF, IDLE oder RUNNING",
                          actual=robot_mode or None, **common))
    results.append(result(prefix + ".program", component,
                          "unknown" if not program_known else ("pass" if mode == "preflight" or program_running else "fail"),
                          "PolyScope-Programm: " + (program or "nicht prüfbar"),
                          expected="PLAYING" if mode == "operational" else "STOPPED, PAUSED oder PLAYING",
                          actual={"state": program, "loaded": answers.get("get loaded program", "")}, **common))
    stats = {"text": f"{robot_mode or 'Unbekannt'} · {safety or 'Sicherheit unbekannt'}\n{answers.get('get loaded program', 'Programm unbekannt')}\n{program}",
             "source": source, "observed_at": observation.get("observed_at", utc_now()), "age_seconds": age,
             "robot_mode": robot_mode, "safety": safety, "program": program,
             "loaded_program": answers.get("get loaded program"), "remote": True if remote_value == "true" else False if remote_value == "false" else None,
             "polyscope_version": answers.get("PolyscopeVersion")}
    return results, stats
