"""Shared diagnosis engine: observations run on the selected physical host."""
from concurrent.futures import ThreadPoolExecutor, wait
import json
import os
import queue
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import threading
import time

from . import __version__
from .dashboard import query_dashboard, evaluate_dashboard
from .host import collect_host, collect_sockets
from .logs import collect_integration
from .models import STATUSES, finalize, new_report, result
from .profiles import load_profile
from . import processes



def age_payload(payload, elapsed):
    """Age received evidence and invalidate expired signals without losing history."""
    import copy
    import math

    def number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    if not number(elapsed):
        raise ValueError("Ungültige verstrichene Beobachtungszeit")
    elapsed = max(0.0, elapsed)
    limits = {"mir_battery": 10.0, "mur_battery": 5.0, "lift": 2.0, "dashboard": 6.0,
              "bms_id": 10.0, "controllers": 10.0}
    profile = payload.get("profile", {})
    configured = profile.get("freshness", {}) if isinstance(profile, dict) else {}
    direct = payload.get("freshness", {})
    configured = {**(configured if isinstance(configured, dict) else {}),
                  **(direct if isinstance(direct, dict) else {})}
    for key, value in configured.items():
        if key in limits and number(value) and value > 0:
            limits[key] = value
    if "controllers" not in configured:
        limits["controllers"] = max(10.0, limits["dashboard"])

    def advance(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in ("age_seconds", "last_success_age_sec"):
                    if number(nested) and nested >= 0:
                        value[key] = nested + elapsed
                    elif nested is not None:
                        value[key] = None
                else:
                    advance(nested)
        elif isinstance(value, list):
            for nested in value:
                advance(nested)

    def expired(age, limit):
        return not number(age) or age > limit

    def check_limit(check_id):
        if check_id in ("ros.mir_battery", "ros.mur_battery", "ros.bms_id"):
            return limits[check_id.split(".", 1)[1]]
        if re.fullmatch(r"ros\.lift_[lr]", check_id):
            return limits["lift"]
        if re.fullmatch(r"ros\.ur_[lr]\.state", check_id):
            return limits["dashboard"]
        if re.fullmatch(r"ros\.ur_[lr]\.controllers", check_id):
            return limits["controllers"]
        if re.fullmatch(r"ur\.[lr]\.(dashboard|safety|remote|mode|program)", check_id):
            return limits["dashboard"]
        return None

    advance(payload)
    for item in payload.get("results", []):
        limit = check_limit(item.get("id", ""))
        if limit is None or item.get("status") == "not_applicable":
            continue
        actual = item.get("actual")
        hardware_age = actual.get("last_success_age_sec") if isinstance(actual, dict) else None
        stale = expired(item.get("age_seconds"), limit)
        if item.get("id", "").startswith("ros.lift_") and hardware_age is not None:
            stale = stale or expired(hardware_age, limit)
        if stale and item.get("status") == "unknown" and item.get("age_seconds") is None:
            continue
        if stale:
            item.setdefault("previous_status", item.get("status"))
            item["status"] = "unknown"
            item["stale"] = True
            item["freshness_limit_seconds"] = limit
            if not item.get("summary", "").startswith("Veraltet"):
                item["summary"] = "Veraltet oder ohne gültiges Datenalter: " + item.get("summary", "")

    stats = payload.get("stats", {})
    for key, limit_key in (("mir_battery", "mir_battery"), ("mur_battery", "mur_battery"),
                           ("lift_l", "lift"), ("lift_r", "lift"),
                           ("ur_l", "dashboard"), ("ur_r", "dashboard")):
        stat = stats.get(key)
        if not isinstance(stat, dict) or stat.get("status") == "not_applicable":
            continue
        nested_ros = stat.get("ros")
        stale = expired(stat.get("age_seconds"), limits[limit_key])
        if isinstance(nested_ros, dict) and nested_ros.get("status") != "not_applicable" and nested_ros.get("age_seconds") is not None:
            stale = stale or expired(nested_ros.get("age_seconds"), limits[limit_key])
        related = next((item for item in payload.get("results", [])
                        if item.get("id") == "ros." + key), None)
        stale = stale or bool(related and related.get("stale"))
        if stale and stat.get("status") == "unknown" and stat.get("age_seconds") is None:
            continue
        if stale:
            if "last_observed" not in stat:
                stat["last_observed"] = copy.deepcopy(stat)
            stat.update(status="unknown", stale=True, communication_confirmed=False,
                        liveness_confirmed=False, freshness_limit_seconds=limits[limit_key])
            if not str(stat.get("text", "")).startswith("Veraltet"):
                stat["text"] = "Veraltet · zuletzt: " + str(stat.get("text", "Unbekannt"))
            for field in ("percent", "height_m"):
                if field in stat:
                    stat[field] = None
            for field in ("controllers", "motion_controllers"):
                if field in stat:
                    stat[field] = []
            if isinstance(nested_ros, dict):
                nested_ros.update(status="unknown", communication_confirmed=False, liveness_confirmed=False)
    return payload


def ros_command(workspace, namespace, mode, duration, watch=False, domain_id=None):
    command = ["/usr/bin/python3", "-u", "-m", "match_mobile_diagnostics.ros_collector",
               "--duration", str(duration), "--mode", mode]
    if namespace:
        command += ["--namespace", namespace]
    if domain_id is not None:
        command += ["--domain-id", str(domain_id)]
    if watch:
        command += ["--watch", "--interval", "0.5"]
    # Failure to source an overlay must not prevent the collector reporting what is missing.
    setup = "\n".join(f"if [ -f {shlex.quote(str(path))} ]; then source {shlex.quote(str(path))} >&2; fi"
                      for path in (Path('/opt/ros/jazzy/setup.bash'), Path(workspace)/'install/setup.bash'))
    shell = setup + "\nexec " + shlex.join(command)
    env = os.environ.copy()
    package_parent = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = package_parent + os.pathsep + env.get("PYTHONPATH", "")
    # Preserve actual domain configuration; expected domain is a check, not an automatic fix.
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return ["bash", "--noprofile", "--norc", "-c", shell], env


def unavailable_ros(message):
    return {"namespace": None, "results": [result("ros.collector", "ROS", "unknown", "ROS-Datenerfassung nicht verfügbar",
            actual=message, source="ROS-Collector", knowledge_id="host_setup")], "stats": {}}


def collect_ros(profile, namespace, workspace, mode, duration, domain_id=None):
    argv, env = ros_command(workspace, namespace, mode, duration, domain_id=domain_id)
    try:
        rc, output, err = processes.run(argv, input_text=json.dumps(profile), env=env, timeout=duration + 15)
        if rc != 0:
            return unavailable_ros(err[-1500:] or f"Collector beendet: {rc}")
        data = json.loads(output)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("Ungültiges Collector-Format")
        return data
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return unavailable_ros(str(exc))


class RosStream:
    def __init__(self, profile, namespace, workspace, mode, duration, domain_id=None):
        self.latest = None
        self.updated = 0.0
        self.error = "ROS-Erfassung läuft an"
        self.lock = threading.Lock()
        argv, env = ros_command(workspace, namespace, mode, duration, watch=True, domain_id=domain_id)
        self.process = processes.spawn(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, bufsize=1, env=env)
        self.process.stdin.write(json.dumps(profile))
        self.process.stdin.close()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        threading.Thread(target=self._errors, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            try:
                data = json.loads(line)
                if isinstance(data.get("results"), list):
                    with self.lock:
                        self.latest, self.updated = data, time.monotonic()
            except (ValueError, TypeError, AttributeError):
                self.error = "Ungültige ROS-Collector-Ausgabe"

    def _errors(self):
        for line in self.process.stderr:
            self.error = line.strip()[-1500:]

    def snapshot(self):
        with self.lock:
            elapsed = time.monotonic() - self.updated
            if self.process.poll() is None and self.latest is not None and elapsed < 6:
                return age_payload(json.loads(json.dumps(self.latest)), elapsed)
        return unavailable_ros(self.error if self.process.poll() is None else "ROS-Collector beendet: " + self.error)

    def close(self):
        processes.stop(self.process)
        self.reader.join(timeout=1)


def local_scan(robot, namespace=None, workspace=None, mode="operational", duration=10.0,
               ros_data=None, host_results=None, domain_id=None, ros_supplier=None):
    profile = load_profile(robot)
    workspace = workspace or profile["expected_workspace"]
    domain_id = profile["ros_domain_id"] if domain_id is None else domain_id
    hostname = socket.gethostname().split(".")[0]
    report = new_report(robot, namespace, mode, hostname)
    report["observation_domain_id"] = domain_id
    report["profile"] = {"version": profile["profile_version"], "provenance": profile["provenance"], "freshness": profile["freshness"]}
    if hostname != profile["hostname"]:
        report["results"] = [result("host.identity", "Verbindung", "fail", "Lokaler Host ist nicht der ausgewählte Roboter-PC",
                             expected=profile["hostname"], actual=hostname, source="hostname",
                             next_steps=[f"Vom Laptop --via ssh --robot {robot} verwenden."], knowledge_id="network_address"),
                             result("scan.prerequisites", "Diagnose", "unknown", "Roboterprüfungen wegen falschem Zielhost ausgelassen", source="Diagnosekern")]
        return finalize(report)
    report["results"].append(result("host.identity", "Verbindung", "pass", "Ausgewählter Roboter-PC bestätigt", actual=hostname, source="hostname"))
    owned_stream = None
    if ros_data is None and ros_supplier is None:
        try:
            owned_stream = RosStream(profile, namespace, workspace, mode, duration, domain_id=domain_id)
            ros_supplier = owned_stream.snapshot
        except OSError as exc:
            ros_data = unavailable_ros(str(exc))
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = {}
            if host_results is None:
                jobs["host"] = pool.submit(collect_host, profile, workspace, mode)
                jobs["integration"] = pool.submit(collect_integration, workspace)
            if host_results is not None:
                report["results"].extend(host_results)
            if owned_stream is not None:
                deadline = time.monotonic() + duration + 15
                while owned_stream.latest is None and owned_stream.process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
            jobs["sockets"] = pool.submit(collect_sockets, profile, mode)
            for side in ("l", "r"):
                jobs[side] = pool.submit(query_dashboard, profile["arms"][side]["address"])
            wait(jobs.values())
            if ros_supplier is not None:
                ros_data = ros_supplier()
            ros_received = time.monotonic()
            for name, future in jobs.items():
                try:
                    data = future.result()
                    if name in ("l", "r"):
                        checks, stats = evaluate_dashboard(name, profile, data, mode)
                        report["results"].extend(checks)
                        report["stats"]["ur_" + name] = stats
                    else:
                        report["results"].extend(data)
                except Exception as exc:
                    report["results"].append(result("collector." + name, "Diagnose", "unknown", "Teilprüfung fehlgeschlagen",
                                                    actual=f"{type(exc).__name__}: {exc}", source=name))
    finally:
        if owned_stream is not None:
            owned_stream.close()
    ros_data = ros_data or unavailable_ros("Keine Collector-Antwort")
    ros_data["freshness"] = profile["freshness"]
    ros_data = age_payload(ros_data, time.monotonic() - ros_received)
    report["namespace"] = ros_data.get("namespace") or namespace
    report["results"].extend(ros_data.get("results", []))
    for name, stats in ros_data.get("stats", {}).items():
        if name in report["stats"]:
            dashboard = report["stats"][name]
            dashboard["text"] += "\n" + stats.get("text", "")
            for key in ("controllers", "motion_controllers", "controllers_available"):
                if key in stats:
                    dashboard[key] = stats[key]
            dashboard["ros"] = stats
        else:
            report["stats"][name] = stats
    # A broken network path explains dependent connection observations; retain evidence, suppress duplicate alarms.
    failures = {item["id"] for item in report["results"] if item["status"] == "fail"}
    for side in ("l", "r"):
        prerequisite = next((check for check in ("network.internal_link", "network.internal_address", f"network.{side}.route") if check in failures), None)
        if prerequisite:
            for item in report["results"]:
                if item["id"] in (f"ur.{side}.dashboard", f"ur.{side}.reverse") and item["status"] == "fail":
                    item["status"] = "unknown"
                    item["causes"].insert(0, "Fehlgeschlagene Voraussetzung: " + prerequisite)
    return finalize(report)


def ssh_command(robot, namespace=None, workspace=None, mode="operational", duration=10.0, host=None, watch=False, interval=2.0, domain_id=None):
    target = host or robot
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", target):
        raise ValueError("Ungültiges SSH-Ziel; SSH-Alias oder user@host angeben.")
    profile = load_profile(robot)
    workspace = workspace or profile["expected_workspace"]
    remote_args = ["watch" if watch else "scan", "--robot", robot, "--via", "local", "--mode", mode, "--duration", str(duration),
                   "--workspace", workspace, "--format", "jsonl" if watch else "json",
                   "--domain-id", str(profile["ros_domain_id"] if domain_id is None else domain_id)]
    if watch:
        remote_args += ["--interval", str(interval)]
    if namespace:
        remote_args += ["--namespace", namespace]
    # SSH invokes a shell; quote all arguments explicitly, never interpolate untrusted commands.
    installed = str(Path(workspace)/"install/match_mobile_diagnostics/lib/match_mobile_diagnostics/mur-diagnostics")
    merged = str(Path(workspace)/"install/lib/match_mobile_diagnostics/mur-diagnostics")
    body = "if command -v mur-diagnostics >/dev/null 2>&1; then exec mur-diagnostics " + shlex.join(remote_args)
    body += '; elif [ -x "$HOME/.local/bin/mur-diagnostics" ]; then exec "$HOME/.local/bin/mur-diagnostics" ' + shlex.join(remote_args)
    setup = "source " + shlex.quote(str(Path(workspace)/"install/setup.bash")) + " >&2; exec "
    body += "; elif [ -x " + shlex.quote(installed) + " ]; then " + setup + shlex.quote(installed) + " " + shlex.join(remote_args)
    body += "; elif [ -x " + shlex.quote(merged) + " ]; then " + setup + shlex.quote(merged) + " " + shlex.join(remote_args)
    body += "; else echo 'MUR_DIAGNOSTICS_NOT_INSTALLED' >&2; exit 127; fi"
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=2", target, "bash --noprofile --norc -c " + shlex.quote(body)]
    return target, argv


def validate_remote(data, robot, target):
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("robot") != robot or not isinstance(data.get("results"), list):
        raise ValueError("SSH-Bericht passt nicht zu Schema oder ausgewähltem Roboter")
    for check in data["results"]:
        if not isinstance(check, dict) or check.get("status") not in STATUSES or not all(isinstance(check.get(key), str) for key in ("id", "summary", "component")):
            raise ValueError("Ungültiger Prüfdatensatz im SSH-Bericht")
    if not isinstance(data.get("stats", {}), dict):
        raise ValueError("Ungültige Statuswerte im SSH-Bericht")
    data["target"] = target
    if data.get("tool_version") != __version__:
        data["results"].append(result("ssh.version", "Verbindung", "warn", "Unterschiedliche Versionen des Diagnosekerns",
                                     expected=__version__, actual=data.get("tool_version"), source=target))
    return data


def remote_scan(robot, namespace=None, workspace=None, mode="operational", duration=10.0, host=None, domain_id=None):
    target, argv = ssh_command(robot, namespace, workspace, mode, duration, host, domain_id=domain_id)
    report = new_report(robot, namespace, mode, target)
    try:
        rc, output, error = processes.run(argv, timeout=duration + 45)
        if rc not in (0, 1, 2):
            installed_missing = "MUR_DIAGNOSTICS_NOT_INSTALLED" in error
            report["results"] = [result("ssh.installation" if installed_missing else "ssh.connection", "Verbindung", "unknown",
                "Diagnosekern auf dem Roboter-PC nicht installiert" if installed_missing else "SSH-Diagnose nicht erreichbar",
                actual=error[-1500:], source=target, knowledge_id="host_setup",
                next_steps=["Installationsanleitung auf dem Roboter-PC ausführen; SSH-Schlüssel und known_hosts prüfen."])]
            return finalize(report)
        data = validate_remote(json.loads(output), robot, target)
        return finalize(data)
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired) as exc:
        report["results"] = [result("ssh.report", "Verbindung", "unknown", "SSH-Diagnosebericht nicht verfügbar",
                                    actual=str(exc), source=target, knowledge_id="host_setup")]
        return finalize(report)



def remote_watch(interval=2.0, **options):
    options.pop("via", None)
    target, argv = ssh_command(**options, watch=True, interval=interval)
    process = processes.spawn(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, bufsize=1)
    messages = queue.Queue(maxsize=1)
    errors = []
    closed = threading.Event()
    def reader():
        for line in process.stdout:
            while not closed.is_set():
                try:
                    messages.put((line, time.monotonic()), timeout=0.1)
                    break
                except queue.Full:
                    try:
                        messages.get_nowait()
                    except queue.Empty:
                        pass
        closed.set()
    def error_reader():
        for line in process.stderr:
            errors.append(line.strip()[-1000:])
            del errors[:-5]
    threading.Thread(target=reader, daemon=True).start()
    threading.Thread(target=error_reader, daemon=True).start()
    last_message = time.monotonic()
    try:
        while True:
            try:
                line, received = messages.get(timeout=0.5)
            except queue.Empty:
                if closed.is_set() or time.monotonic() - last_message > 45:
                    raise ConnectionError("SSH-Beobachtung beendet oder ohne Antwort. " + " ".join(errors))
                continue
            data = validate_remote(json.loads(line), options["robot"], target)
            last_message = time.monotonic()
            yield finalize(age_payload(data, time.monotonic() - received))
    except (OSError, ValueError) as exc:
        report = new_report(options["robot"], options.get("namespace"), options.get("mode", "operational"), target)
        report["results"] = [result("ssh.connection", "Verbindung", "unknown", "SSH-Beobachtung unterbrochen",
                                     actual=str(exc), source=target, knowledge_id="host_setup")]
        yield finalize(report)
    finally:
        closed.set()
        processes.stop(process)



def laptop_discovery(report):
    """Compare optional observer graph visibility; it never gates robot-side checks."""
    remote_driver = next((item for item in report["results"] if item["id"] == "ros.driver"), {})
    if not remote_driver.get("actual"):
        return result("network.discovery", "Netzwerk", "not_applicable", "Laptop-Discovery ohne nachgewiesenen Robotertreiber nicht vergleichbar", source="Laptop/Robotervergleich")
    profile = load_profile(report["robot"])
    workspace = os.environ.get("WS", str(Path.home()/"colcon_ws"))
    argv, env = ros_command(workspace, report.get("namespace"), report.get("mode", "operational"), 3.0,
                            domain_id=report.get("observation_domain_id", profile["ros_domain_id"]))
    argv[-1] += " --discovery-only"
    try:
        rc, output, error = processes.run(argv, input_text=json.dumps(profile), env=env, timeout=10)
        data = json.loads(output) if rc == 0 else {"available": False, "error": error[-1000:]}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        data = {"available": False, "error": str(exc)}
    if not data.get("available"):
        return result("network.discovery", "Netzwerk", "not_applicable", "Optionaler Laptop-ROS-Vergleich nicht verfügbar",
                      actual=data.get("error"), source="Laptop-ROS", knowledge_id="ros_discovery")
    visible = bool(data.get("visible"))
    return result("network.discovery", "Netzwerk", "pass" if visible else "warn",
                  "Roboterdaten auf dem Laptop sichtbar" if visible else "Treiber auf Roboter sichtbar, auf Laptop nicht",
                  expected={"namespace": report.get("namespace"), "domain": profile["ros_domain_id"]},
                  actual={"visible": visible, "domain": data.get("domain_id")}, source="Laptop/Robotervergleich",
                  next_steps=[] if visible else ["Domain-ID, RMW, statische Peers und Netzwerk-Discovery des Laptops prüfen."],
                  knowledge_id="ros_discovery")


def scan(**options):
    via = options.pop("via", "local")
    if via == "ssh":
        probe = new_report(options["robot"], options.get("namespace") or "/" + options["robot"], options.get("mode", "operational"), "laptop")
        probe["observation_domain_id"] = options.get("domain_id") if options.get("domain_id") is not None else load_profile(options["robot"])["ros_domain_id"]
        probe["results"] = [{"id": "ros.driver", "actual": ["comparison pending"]}]
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(laptop_discovery, probe)
            report = remote_scan(**options)
            received = time.monotonic()
            remote_driver = next((r for r in report["results"] if r["id"] == "ros.driver"), {})
            comparison = pending.result() if remote_driver.get("actual") else laptop_discovery(report)
        age_payload(report, time.monotonic() - received)
        report["results"].append(comparison)
        return finalize(report)
    options.pop("host", None)
    return local_scan(**options)


def watch(interval=2.0, **options):
    if options.get("via") == "ssh":
        updated, comparison, pending = 0.0, None, None
        with ThreadPoolExecutor(max_workers=1) as pool:
            for report in remote_watch(interval=interval, **options):
                if pending is not None and pending.done():
                    comparison = pending.result()
                    updated, pending = time.monotonic(), None
                if pending is None and (comparison is None or time.monotonic() - updated > 30 or
                        comparison.get("status") == "not_applicable" and any(r["id"] == "ros.driver" and r.get("actual") for r in report["results"])):
                    pending = pool.submit(laptop_discovery, json.loads(json.dumps(report)))
                report["results"].append(comparison or result("network.discovery", "Netzwerk", "unknown", "Optionaler Laptop-Discovery-Vergleich läuft", source="Laptop/Robotervergleich"))
                yield finalize(report)
        return
    local_options = {k: v for k, v in options.items() if k not in ("via", "host")}
    profile = load_profile(options["robot"])
    if socket.gethostname().split(".")[0] != profile["hostname"]:
        yield local_scan(**local_options)
        return
    stream = RosStream(profile, options.get("namespace"), options.get("workspace") or profile["expected_workspace"],
                       options.get("mode", "operational"), options.get("duration", 10.0),
                       domain_id=profile["ros_domain_id"] if options.get("domain_id") is None else options["domain_id"])
    cached_host, updated = None, 0.0
    try:
        while True:
            started = time.monotonic()
            if started - updated >= 30:
                cached_host = None
            report = local_scan(**local_options, ros_supplier=stream.snapshot, host_results=cached_host)
            if cached_host is None:
                cached_host = [item for item in report["results"] if item["component"] in ("Netzwerk", "Betriebssystem", "Treiberintegration", "Batterien", "Hubsäulen") and not item["id"].startswith("ros.") and item["source"] != "ROS"]
                # Cache only actual host checks, never periodic ROS telemetry.
                host_ids = {"battery.can", "lift.l.device", "lift.r.device", "lift.mapping"}
                cached_host = [item for item in cached_host if item["id"].startswith(("network.", "host.", "installation.", "logs.")) or item["id"] in host_ids]
                updated = started
            yield report
            time.sleep(max(0.1, interval - (time.monotonic()-started)))
    finally:
        stream.close()
