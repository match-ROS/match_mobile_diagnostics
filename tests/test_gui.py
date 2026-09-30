"""Qt tests exercise process boundaries, robot isolation and actual exports."""

import json
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt5")
from PyQt5 import QtCore, QtWidgets

from match_mobile_diagnostics.gui import DiagnosticWindow


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application


@pytest.fixture
def window(app, monkeypatch):
    monkeypatch.setattr(DiagnosticWindow, "_load_articles", staticmethod(lambda: [
        {"id": "ur.remote", "title": "UR nicht in Remote", "symptoms": ["Remote"],
         "body": "Am Bediengerät den Betriebsmodus prüfen.", "checks": ["ur_l.remote"],
         "manual_steps": ["Betriebsmodus am Pendant geprüft"]},
    ]))
    result = DiagnosticWindow()
    yield result
    processes = result.findChildren(QtCore.QProcess)
    result.close()
    for process in processes:
        try:
            if process.state() != QtCore.QProcess.NotRunning:
                process.kill()
                process.waitForFinished(1000)
        except RuntimeError:
            pass
    result.deleteLater()
    app.processEvents()


def wait_for(app, predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "Qt condition did not complete before timeout"


def report(robot="mur620d"):
    return {
        "schema_version": 1, "tool_version": "0.1.0", "robot": robot,
        "namespace": robot, "target": "local", "mode": "operational",
        "checked_at": "2026-09-29T12:00:00+00:00", "complete": True,
        "summary": "Ein Problem gefunden", "counts": {"pass": 1, "fail": 1},
        "results": [
            {"id": "ur_l.remote", "component": "UR10_l", "status": "fail",
             "summary": "Linker UR ist nicht in Remote", "expected": True, "actual": False,
             "source": "UR-Dashboard", "observed_at": "2026-09-29T12:00:00+00:00",
             "age_seconds": 0.2, "evidence": ["Remote control: false"],
             "causes": ["Lokaler Betriebsmodus"], "next_steps": ["Betriebsmodus am Pendant prüfen"],
             "knowledge_id": "ur.remote"},
            {"id": "host.route", "component": "Netzwerk", "status": "pass",
             "summary": "Route korrekt", "actual": "192.168.12.69", "expected": "192.168.12.69"},
        ],
        "stats": {"mir_battery": {"text": "76 %", "source": "battery_state", "age_seconds": 1.2},
                  "ur_l": {"text": "Lokaler Modus · Programm gestoppt"}},
        "manual_observations": [],
    }


def test_report_renders_evidence_and_symptom_filter(window):
    window.apply_report(report())
    assert window.stat_labels["mir_battery"].text() == "76 %"
    assert "battery_state" in window.stat_labels["mir_battery"].toolTip()
    assert window.stat_labels["lift_l"].text() == "Nicht erfasst"
    assert window.results_tree.topLevelItemCount() == 2
    details = window.details.toPlainText()
    assert all(text in details for text in ["Soll", "Ist", "UR-Dashboard", "Remote control: false",
                                            "Lokaler Betriebsmodus", "Betriebsmodus am Pendant prüfen"])
    window.symptom_combo.setCurrentIndex(1)
    assert window.results_tree.topLevelItemCount() == 1
    assert window.results_tree.topLevelItem(0).text(0) == "UR10_l"


def test_selection_cancels_process_and_cannot_accept_old_robot_report(window, app, monkeypatch):
    window.apply_report(report())
    window.observation_edit.setPlainText("Stecker am linken UR geprüft")
    window.add_observation()
    script = "import time; time.sleep(10)"
    monkeypatch.setattr(window, "command_arguments", lambda watch: ["-u", "-c", script])
    window.start_scan()
    old_process = window._process
    generation = window._generation
    wait_for(app, lambda: old_process.state() == QtCore.QProcess.Running)
    window.robot_combo.setCurrentText("mur620c")
    window.apply_report(report(), generation)
    assert window._last_report is None
    assert not window.export_button.isEnabled()
    assert window.results_tree.topLevelItemCount() == 0
    assert window._manual_observations == []
    assert all(label.text() == "Noch keine Daten" for label in window.stat_labels.values())
    wait_for(app, lambda: not any(p.state() != QtCore.QProcess.NotRunning
                                for p in window.findChildren(QtCore.QProcess)))
    with pytest.raises(ValueError, match="Roboter"):
        window.apply_report(report())


def test_watch_handles_split_unicode_and_disconnect_without_stale_cards(window, app, monkeypatch):
    value = report()
    value["stats"]["ur_l"]["text"] = "Not-Halt · Hubsäule"
    payload = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
    split = payload.index("ä".encode("utf-8")) + 1
    script = (
        "import sys,time; "
        f"sys.stdout.buffer.write({payload[:split]!r}); sys.stdout.buffer.flush(); time.sleep(.1); "
        f"sys.stdout.buffer.write({payload[split:]!r}); sys.stdout.buffer.flush(); time.sleep(.3)"
    )
    monkeypatch.setattr(window, "command_arguments", lambda watch: ["-u", "-c", script])
    window.start_scan(watch=True)
    wait_for(app, lambda: window._last_report is not None)
    assert window.stat_labels["ur_l"].text() == "Not-Halt · Hubsäule"
    wait_for(app, lambda: not window._running)
    assert all(label.text() == "Live-Verbindung beendet" for label in window.stat_labels.values())
    assert window.export_data()["stats"]["ur_l"]["text"] == "Not-Halt · Hubsäule"


def test_scan_accepts_diagnostic_nonzero_exit_and_rejects_invalid_output(window, app, monkeypatch):
    script = f"import sys; print({json.dumps(report())!r}); sys.exit(2)"
    monkeypatch.setattr(window, "command_arguments", lambda watch: ["-u", "-c", script])
    window.start_scan()
    wait_for(app, lambda: not window._running)
    assert window._last_report["counts"]["fail"] == 1
    assert window.stat_labels["mir_battery"].text() == "76 %"
    monkeypatch.setattr(window, "command_arguments", lambda watch: ["-u", "-c", "print('broken JSON')"])
    window.start_scan()
    wait_for(app, lambda: not window._running)
    assert window._last_report is None
    assert not window.export_button.isEnabled()
    assert window.results_tree.topLevelItemCount() == 0


def test_json_and_markdown_exports_include_scoped_user_observations(window, tmp_path):
    window.apply_report(report())
    window.observation_edit.setPlainText("Ethernet-Stecker geprüft")
    window.add_observation()
    window._manual_observations.append({
        "robot": "mur620c", "session_id": window._session_id, "text": "Wrong robot"})
    window._manual_observations.append({
        "robot": "mur620d", "session_id": "old", "text": "Wrong session"})
    json_path, markdown_path = tmp_path / "report.json", tmp_path / "report.md"
    window.export_report(json_path)
    window.export_report(markdown_path)
    exported = json.loads(json_path.read_text())
    assert len(exported["manual_observations"]) == 1
    observation = exported["manual_observations"][0]
    assert observation["source"] == "user_reported"
    assert observation["text"] == "Ethernet-Stecker geprüft"
    markdown = markdown_path.read_text()
    assert "Ethernet-Stecker geprüft" in markdown
    assert "Wrong robot" not in markdown and "Wrong session" not in markdown
    assert window._last_report["manual_observations"] == []


def test_stop_discards_late_report_and_command_uses_argument_boundaries(window, app, monkeypatch):
    window.host_edit.setText("robot-alias")
    window.workspace_edit.setText("/some workspace/with spaces")
    args = window.command_arguments(True)
    assert args[args.index("--workspace") + 1] == "/some workspace/with spaces"
    assert args[args.index("--host") + 1] == "robot-alias"
    assert args[args.index("--format") + 1] == "jsonl"
    assert args[args.index("--domain-id") + 1] == "62"
    script = "import time; time.sleep(10)"
    monkeypatch.setattr(window, "command_arguments", lambda watch: ["-u", "-c", script])
    window.start_scan(True)
    generation = window._generation
    wait_for(app, lambda: window._process.state() == QtCore.QProcess.Running)
    window.stop_scan()
    window.apply_report(report(), generation)
    assert window._last_report is None
    assert not window._running
    assert all(label.text() == "Beobachtung gestoppt" for label in window.stat_labels.values())


def test_checklist_confirmation_is_user_evidence_and_unchecking_removes_it(window):
    window.apply_report(report())
    step = window.manual_steps.item(0)
    step.setCheckState(QtCore.Qt.Checked)
    data = window.export_data()
    assert data["manual_observations"][0]["source"] == "user_reported"
    assert data["manual_observations"][0]["manual_step"] == "Betriebsmodus am Pendant geprüft"
    assert data["results"][0]["status"] == "fail"
    step.setCheckState(QtCore.Qt.Unchecked)
    assert window.export_data()["manual_observations"] == []
    step.setCheckState(QtCore.Qt.Checked)
    window.robot_combo.setCurrentText("mur620c")
    assert window.manual_steps.item(0).checkState() == QtCore.Qt.Unchecked
    assert window._manual_observations == []


def test_live_stats_expire_while_report_evidence_remains_exportable(window):
    window.apply_report(report())
    window._watching = True
    window._running = True
    window._report_received_at = time.monotonic() - 20.0
    window._refresh_freshness()
    assert window.stat_labels["mir_battery"].text().startswith("Veraltet")
    assert window.export_data()["stats"]["mir_battery"]["text"] == "76 %"
    window.apply_report(report())
    assert window.stat_labels["mir_battery"].text() == "76 %"


def test_domain_change_invalidates_snapshot_and_manual_session(window):
    window.apply_report(report())
    window.manual_steps.item(0).setCheckState(QtCore.Qt.Checked)
    generation = window._generation
    window.domain_spin.setValue(63)
    window.apply_report(report(), generation)
    assert window._last_report is None
    assert window._manual_observations == []
    assert not window.export_button.isEnabled()
    assert all(label.text() == "Noch keine Daten" for label in window.stat_labels.values())
    arguments = window.command_arguments()
    assert arguments[arguments.index("--domain-id") + 1] == "63"


def test_ur_card_distinguishes_unavailable_controller_query_from_confirmed_empty(window):
    data = report()
    data["stats"]["ur_l"] = {
        "text": "RUNNING / NORMAL", "remote": False,
        "controllers": [], "controllers_available": False,
    }
    window.apply_report(data)
    text = window.stat_labels["ur_l"].text()
    assert "Lokaler Modus" in text
    assert "Controller nicht prüfbar" in text
    assert "kein aktiver Bewegungscontroller" not in text
    data["stats"]["ur_l"]["controllers_available"] = True
    data["stats"]["ur_l"]["remote"] = True
    window.apply_report(data)
    text = window.stat_labels["ur_l"].text()
    assert "kein aktiver Bewegungscontroller" in text
    assert "Controller nicht prüfbar" not in text
    assert "Remote" in text and "Lokaler Modus" not in text
    data["stats"]["ur_l"].pop("controllers_available")
    data["stats"]["ur_l"]["ros"] = {"controllers_available": False}
    window.apply_report(data)
    assert "Controller nicht prüfbar" in window.stat_labels["ur_l"].text()


def test_robot_pc_starts_with_matching_local_target(app, monkeypatch):
    monkeypatch.setattr("match_mobile_diagnostics.gui.socket.gethostname", lambda: "mur620a")
    diagnostic = DiagnosticWindow()
    try:
        assert diagnostic.robot_combo.currentText() == "mur620a"
        assert diagnostic.via_combo.currentData() == "local"
    finally:
        diagnostic.close()
        diagnostic.deleteLater()
        app.processEvents()


def test_scan_child_can_import_package_outside_checkout(window, app, tmp_path, monkeypatch):
    import socket
    local_host = socket.gethostname().split(".")[0]
    robot = next(name for name in ("mur620a", "mur620b", "mur620c", "mur620d") if name != local_host)
    monkeypatch.chdir(tmp_path)
    window.robot_combo.setCurrentText(robot)
    window.via_combo.setCurrentIndex(1)
    window.start_scan()
    wait_for(app, lambda: not window._running, timeout=6.0)
    assert window._last_report is not None, window.process_log.toPlainText()
    assert window._last_report["robot"] == robot
    assert any(item["id"] == "host.identity" for item in window._last_report["results"])
    assert "ModuleNotFoundError" not in window.process_log.toPlainText()


def test_clock_card_displays_robot_observer_and_bounded_difference(window):
    value = report()
    value['stats']['clock'] = {
        'text': 'Roboter und GUI: 10.145.8.50 synchron\nDifferenz: +2.0 ± 4.0 ms',
        'status': 'pass', 'source': 'Chrony/SSH-Zeitprobe', 'observed_at': '2026-09-30T10:00:00+00:00',
        'age_seconds': 0.2,
    }
    window.apply_report(value)
    assert 'Roboter und GUI: 10.145.8.50 synchron' in window.stat_labels['clock'].text()
    assert '+2.0 ± 4.0 ms' in window.stat_labels['clock'].text()
    assert 'Chrony/SSH-Zeitprobe' in window.stat_labels['clock'].toolTip()
