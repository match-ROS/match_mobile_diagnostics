"""Bounded, read-only Linux host observations. Devices are never opened."""
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
from .models import result


def command(argv, timeout=3.0):
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, errors="replace")
        return {"returncode": completed.returncode, "stdout": completed.stdout[-65536:], "stderr": completed.stderr[-2000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": -1, "stdout": "", "stderr": str(exc)}


def json_command(argv):
    response = command(argv)
    try:
        return json.loads(response["stdout"]), response
    except (ValueError, TypeError):
        return None, response


def evaluate_route(side, profile, routes, error=""):
    arm = profile["arms"][side]
    expected = {"dev": profile["robot_interface"], "src": profile["reverse_ip"], "dst": arm["address"]}
    if routes is None:
        return result(f"network.{side}.route", "Netzwerk", "unknown", "Route konnte nicht gelesen werden",
                      expected=expected, actual=error, source="ip -j route get", knowledge_id="network_address")
    route = routes[0] if routes else {}
    actual = {"dev": route.get("dev"), "src": route.get("prefsrc", route.get("src")), "dst": arm["address"]}
    ok = actual == expected
    return result(f"network.{side}.route", "Netzwerk", "pass" if ok else "fail",
                  f"Route zu {arm['hostname']} korrekt" if ok else f"Falscher Netzwerkpfad zu {arm['hostname']}",
                  expected=expected, actual=actual, source="ip -j route get", knowledge_id="network_address",
                  causes=[] if ok else ["Interne PC-Adresse oder Routenkonfiguration weicht vom Soll ab."],
                  next_steps=[] if ok else [f"Am Roboter-PC die Adresse {profile['robot_address']} und die NetworkManager-Verbindung prüfen."])


def collect_host(profile, workspace, mode="operational"):
    results = []
    iface = profile["robot_interface"]
    addresses, response = json_command(["ip", "-j", "address", "show", "dev", iface])
    if addresses:
        interface = addresses[0]
        actual_addresses = [f"{entry['local']}/{entry['prefixlen']}" for entry in interface.get("addr_info", []) if entry.get("family") == "inet"]
        flags = interface.get("flags", [])
        results.append(result("network.internal_link", "Netzwerk", "pass" if "LOWER_UP" in flags else "fail",
                              "Interner Ethernet-Link", expected="LOWER_UP", actual=flags, source=f"ip address show {iface}",
                              knowledge_id="network_address", next_steps=[] if "LOWER_UP" in flags else ["Kabel, Switch-Versorgung und Link-LEDs prüfen."]))
        results.append(result("network.internal_address", "Netzwerk", "pass" if profile["robot_address"] in actual_addresses else "fail",
                              "Interne PC-Adresse", expected=profile["robot_address"], actual=actual_addresses,
                              source=f"ip address show {iface}", knowledge_id="network_address"))
    else:
        results.append(result("network.internal_link", "Netzwerk", "fail" if addresses == [] or response["returncode"] == 1 else "unknown",
                              "Interne Netzwerkschnittstelle nicht verfügbar", expected=iface, actual=response["stderr"],
                              source="ip address", knowledge_id="network_address"))
    for side, arm in profile["arms"].items():
        routes, response = json_command(["ip", "-j", "route", "get", arm["address"]])
        results.append(evaluate_route(side, profile, routes, response["stderr"]))
        response = command(["getent", "ahostsv4", arm["hostname"]])
        values = sorted({line.split()[0] for line in response["stdout"].splitlines() if line.split()})
        results.append(result(f"network.{side}.hostname", "Netzwerk", "pass" if values == [arm["address"]] else "fail",
                              f"Namensauflösung {arm['hostname']}", expected=arm["address"], actual=values,
                              source="getent ahostsv4", knowledge_id="network_address"))
    management, response = json_command(["ip", "-j", "address", "show", "dev", profile["management_interface"]])
    actual_management = [f"{a['local']}/{a['prefixlen']}" for entry in management or [] for a in entry.get("addr_info", []) if a.get("family") == "inet"]
    results.append(result("network.management_address", "Netzwerk", "unknown" if management is None else
                          ("pass" if profile["management_address"] in actual_management else "warn"),
                          "Management-Adresse", expected=profile["management_address"], actual=actual_management,
                          source="ip address", knowledge_id="management_dhcp"))
    nm = command(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", profile["management_interface"]])
    connection_name = nm["stdout"].strip()
    if nm["returncode"] == 0 and connection_name and connection_name != "--":
        method = command(["nmcli", "-g", "ipv4.method", "connection", "show", connection_name])
        if method["returncode"] == 0:
            results.append(result("network.management_method", "Netzwerk", "pass" if method["stdout"].strip() == "manual" else "warn",
                                  "Statische Management-Konfiguration", expected="manual", actual=method["stdout"].strip(),
                                  source="nmcli connection show", knowledge_id="management_dhcp"))
    journal = command(["journalctl", "-b", "-u", "NetworkManager", "--since", "-5 min", "-n", "80", "-o", "short-unix", "--no-pager"])
    import re
    clues = [line for line in journal["stdout"].splitlines() if re.search(r"duplicate|conflict|dad-failed|dhcp.*(?:timeout|failed)", line, re.I)]
    if clues:
        results.append(result("network.recent_conflicts", "Netzwerk", "warn", "Aktuelle NetworkManager-Hinweise",
                              actual=clues[-5:], source="NetworkManager, aktueller Boot, letzte 5 Minuten",
                              knowledge_id="management_dhcp", causes=["IP-Konflikt oder DHCP-Problem möglich; Meldung und betroffene Schnittstelle prüfen."]))
    kernel = platform.release()
    rt_marker = Path("/sys/kernel/realtime")
    rt = (rt_marker.exists() and rt_marker.read_text().strip() == "1") or "PREEMPT_RT" in platform.version() or "-rt" in kernel
    results.append(result("host.realtime_kernel", "Betriebssystem", "pass" if rt else "warn", "Aktiv gebooteter Echtzeitkernel",
                          expected="PREEMPT_RT", actual=kernel, source="uname + /sys/kernel/realtime", knowledge_id="host_setup"))
    rtprio = resource.getrlimit(resource.RLIMIT_RTPRIO)[0]
    memlock = resource.getrlimit(resource.RLIMIT_MEMLOCK)[0]
    results.append(result("host.realtime_limits", "Betriebssystem", "pass" if rtprio >= 99 and memlock == resource.RLIM_INFINITY else "warn",
                          "Echtzeitrechte der Diagnosesitzung", expected={"rtprio": 99, "memlock": "unlimited"},
                          actual={"rtprio": rtprio, "memlock": memlock}, source="getrlimit (Diagnoseprozess)",
                          evidence=["Rechte eines separat gestarteten Treiberprozesses können abweichen."], knowledge_id="host_setup"))
    for check_id, path in (("host.ros_setup", Path("/opt/ros/jazzy/setup.bash")),
                           ("host.workspace_setup", Path(workspace)/"install/setup.bash")):
        results.append(result(check_id, "Betriebssystem", "pass" if path.is_file() else "fail", "ROS-/Workspace-Setup",
                              expected=str(path), actual="vorhanden" if path.is_file() else "fehlt", source="Dateisystem", knowledge_id="host_setup"))
    dependency = command(["/usr/bin/python3", "-c", "import importlib.util; print('available' if importlib.util.find_spec('can') else 'missing')"])
    available = dependency["stdout"].strip() == "available"
    results.append(result("host.python_can", "Betriebssystem", "pass" if available else "fail",
                          "Python-CAN-Abhängigkeit", expected="python-can verfügbar", actual=dependency["stdout"].strip() or dependency["stderr"],
                          source="System-Python importlib.util.find_spec", knowledge_id="host_setup"))
    can_iface = profile["can_interface"]
    can, response = json_command(["ip", "-d", "-j", "link", "show", "dev", can_iface])
    if can:
        link = can[0]
        info = link.get("linkinfo", {}).get("info_data", {})
        bitrate = info.get("bittiming", {}).get("bitrate", info.get("bitrate"))
        up = "UP" in link.get("flags", [])
        can_state = info.get("state")
        if not up or (bitrate is not None and bitrate != profile["can_bitrate"]) or can_state in ("BUS-OFF", "STOPPED", "SLEEPING"):
            status = "fail" if mode == "operational" else "warn"
        elif bitrate is None or can_state not in ("ERROR-ACTIVE", "ERROR-WARNING", "ERROR-PASSIVE"):
            status = "unknown"
        elif can_state in ("ERROR-WARNING", "ERROR-PASSIVE"):
            status = "warn"
        else:
            # SocketCAN ERROR-ACTIVE is the healthy state, despite its name.
            status = "pass"
        results.append(result("battery.can", "Batterien", status,
                              "BMS-CAN-Schnittstelle", expected={"up": True, "state": "ERROR-ACTIVE", "bitrate": profile["can_bitrate"]},
                              actual={"up": up, "state": info.get("state"), "bitrate": bitrate}, source="ip -d link", knowledge_id="battery_can"))
    else:
        results.append(result("battery.can", "Batterien", "fail" if not Path('/sys/class/net', can_iface).exists() else "unknown",
                              "BMS-CAN-Schnittstelle nicht lesbar", expected=can_iface, actual=response["stderr"],
                              source="ip -d link", knowledge_id="battery_can"))
    for side in ("l", "r"):
        if not profile["has_lifts"]:
            results.append(result(f"lift.{side}.device", "Hubsäulen", "not_applicable", "Roboter hat keine Hubsäulen", source="Sollprofil"))
            continue
        path = profile["lift_ports"][side]
        exists = Path(path).exists()
        access = exists and os.access(path, os.R_OK | os.W_OK)
        results.append(result(f"lift.{side}.device", "Hubsäulen", "pass" if access else "fail",
                              "Serielles Gerät " + ("links" if side == "l" else "rechts"), expected=path,
                              actual={"exists": exists, "read_write_permission": access}, source="stat/access; Gerät wird nicht geöffnet", knowledge_id="lift_serial"))
    if profile["has_lifts"] and profile["provenance"]["lift_ports"]["status"] == "provisional":
        results.append(result("lift.mapping", "Hubsäulen", "warn", "L/R-USB-Zuordnung im Sollprofil noch vorläufig",
                              actual=profile["lift_ports"], source="Sollprofil", knowledge_id="lift_serial",
                              next_steps=["Adapterkennungen durch Kabelverfolgung links/rechts bestätigen und Sollprofil versioniert aktualisieren."]))
    return results


def collect_sockets(profile, mode):
    response = command(["ss", "-H", "-n", "-t", "-a"])
    results = []
    for side, arm in profile["arms"].items():
        reverse_port = arm["reverse_ports"][0]
        listening, connected = False, False
        matching = []
        for line in response["stdout"].splitlines():
            columns = line.split()
            if len(columns) < 5:
                continue
            state, local, peer = columns[0], columns[3], columns[4]
            if local.rsplit(":", 1)[-1] == str(reverse_port):
                listening |= state == "LISTEN"
                if state in ("ESTAB", "ESTABLISHED") and peer.rsplit(":", 1)[0] == arm["address"]:
                    connected = True
                matching.append(line)
        status = "unknown" if response["returncode"] else ("pass" if connected or mode == "preflight" else "fail")
        results.append(result(f"ur.{side}.reverse", "UR " + ("links" if side == "l" else "rechts"), status,
                              "Reverse-Verbindung " + ("vorhanden" if connected else "nicht vorhanden"),
                              expected="Verbindung zum UR" if mode == "operational" else "Treiber darf noch gestoppt sein",
                              actual={"port": reverse_port, "listener": listening, "connected": connected},
                              source="ss -H -n -t -a", evidence=matching[:4], knowledge_id="ur_reverse"))
    return results
