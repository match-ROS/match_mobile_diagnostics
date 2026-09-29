"""Time-scoped evidence; old log lines are never current hardware state."""
from datetime import datetime, timezone
from pathlib import Path
import re
import time
from .models import result

PATTERNS = (
    ("calibration", r"calibration.*(?:doesn.t match|mismatch|differs)|kinematics.*mismatch", "Kalibrierungsabweichung", "driver_installation"),
    ("serial", r"Failed to cycle2|Failed to open port|Permission denied.*tty", "Serielle Kommunikation", "lift_serial"),
    ("reverse", r"Connection to reverse interface dropped|Receive Program Failed", "Reverse-Verbindung unterbrochen", "ur_reverse"),
    ("dependencies", r"No module named|PackageNotFoundError|No such file.*(?:xacro|launch)|'bool' object is not callable", "Treiber-/Installationsfehler", "driver_installation"),
    ("realtime", r"Could not enable FIFO|missed its desired rate|overruns:", "Echtzeit-/Scheduling-Hinweis", "host_setup"),
    ("bms", r"Failed to send BMS|Network is down|bms_can_down", "BMS-Kommunikation", "battery_can"),
)


def line_timestamp(line):
    match = re.search(r"\[(1[6-9]\d{8}(?:\.\d+)?)\]", line)
    if match:
        return float(match.group(1))
    match = re.search(r"\b(20\d\d-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)?)", line)
    if match:
        try:
            timestamp = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
            # Unzoned wall time is ambiguous; do not pretend it is UTC.
            return timestamp.timestamp() if timestamp.tzinfo else None
        except ValueError:
            pass
    return None


def analyze_log(text, source, now=None):
    now = time.time() if now is None else now
    results, history = [], 0
    for name, pattern, title, knowledge in PATTERNS:
        current = []
        for line in text.splitlines():
            if not re.search(pattern, line, re.I):
                continue
            stamp = line_timestamp(line)
            if stamp is not None and 0 <= now - stamp <= 600:
                current.append(line[-1000:])
            else:
                history += 1
        if current:
            results.append(result("logs." + name, "Treiberintegration", "warn", title + " im aktuellen Logzeitfenster",
                                  source=source, evidence=current[-5:], actual="Hinweis der letzten 10 Minuten",
                                  causes=["Loghinweis; aktuelle Statusabfragen entscheiden über den gegenwärtigen Zustand."], knowledge_id=knowledge))
    results.append(result("logs.window", "Treiberintegration", "pass", "Loghinweise zeitlich eingeordnet",
                          actual={"historical_or_undated_matches": history}, source=source,
                          evidence=["Alte und undatierte Zeilen begründen keinen aktuellen Roboterfehler."]))
    return results


def collect_integration(workspace):
    root = Path(workspace)
    results = []
    candidates = [root/"install/mur_launch_hardware/share/mur_launch_hardware/launch/mur_620.launch.py",
                  root/"install/ur_simulation_gz/share/ur_simulation_gz/urdf/ur_gz.ros2_control.xacro"]
    # Both isolated and merged colcon installs are supported.
    for path in candidates:
        merged = root/"install/share"/path.relative_to(root/"install").parts[2]/Path(*path.relative_to(root/"install").parts[3:])
        found = path.is_file() or merged.is_file()
        results.append(result("installation." + path.stem, "Treiberintegration", "pass" if found else "fail",
                              "Installierte Startdatei: " + path.name, expected=str(path),
                              actual="vorhanden" if found else "fehlt oder Symlink-Ziel fehlt", source="Dateisystem", knowledge_id="driver_installation"))
    log = root/"src/match_mobile_robotics_jazzy/logs/hardware/latest.log"
    try:
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 512000))
            text = stream.read(512000).decode("utf-8", errors="replace")
        results += analyze_log(text, str(log))
    except OSError as exc:
        results.append(result("logs.available", "Treiberintegration", "unknown", "Hardwarelog nicht lesbar",
                              actual=str(exc), source=str(log), knowledge_id="driver_installation"))
    return results
