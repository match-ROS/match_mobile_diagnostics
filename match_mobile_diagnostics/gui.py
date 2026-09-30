"""Standalone diagnostic viewer. Qt is optional; this module never imports ROS."""

from __future__ import annotations

import copy
import codecs
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import socket
import sys
import time
import uuid

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except ImportError:
    QtCore = QtGui = QtWidgets = None


STATUS_LABELS = {
    "pass": "OK", "warn": "Hinweis", "fail": "Fehler", "unknown": "Unbekannt",
    "not_applicable": "Nicht zutreffend",
}
STATUS_COLORS = {
    "pass": "#24734b", "warn": "#94610b", "fail": "#b52d36",
    "unknown": "#586779", "not_applicable": "#77818c",
}
STAT_LABELS = {
    "mir_battery": "MiR · Akku", "mur_battery": "MuR · Akku",
    "ur_l": "UR10 links", "ur_r": "UR10 rechts",
    "lift_l": "Hubsäule links", "lift_r": "Hubsäule rechts",
    "clock": "Zeitsynchronisation",
}


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _display(value):
    if value is None or value == "":
        return "Keine Angabe"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def _escape(value):
    return html.escape(_display(value)).replace("\n", "<br>")


if QtWidgets is not None:
    class DiagnosticWindow(QtWidgets.QMainWindow):
        """View reports produced by the same CLI used by agents and operators."""

        def __init__(self):
            super().__init__()
            self.setWindowTitle("MuR · Diagnose")
            self.resize(1180, 860)
            self._process = None
            self._generation = 0
            self._session_id = uuid.uuid4().hex
            self._last_report = None
            self._manual_observations = []
            self._running = False
            self._watching = False
            self._report_received_at = None
            self._articles = self._load_articles()
            self._build_ui()
            self._populate_articles()
            self._set_running(False)
            self._freshness_timer = QtCore.QTimer(self)
            self._freshness_timer.setInterval(1000)
            self._freshness_timer.timeout.connect(self._refresh_freshness)
            self._freshness_timer.start()

        @staticmethod
        def _load_articles():
            from .knowledge import list_articles
            return list_articles()

        def _build_ui(self):
            central = QtWidgets.QWidget()
            self.setCentralWidget(central)
            root = QtWidgets.QVBoxLayout(central)
            root.setContentsMargins(20, 18, 20, 16)
            root.setSpacing(12)
            heading = QtWidgets.QLabel("MuR Diagnose")
            heading.setStyleSheet("font-size: 24px; font-weight: 600; color: #20364d;")
            root.addWidget(heading)
            description = QtWidgets.QLabel(
                "Hardware, Netzwerk und Treiber prüfen · Befunde verstehen · Wissen weitergeben")
            description.setStyleSheet("color: #52657a;")
            root.addWidget(description)

            selection = QtWidgets.QGroupBox("Diagnoseziel")
            layout = QtWidgets.QGridLayout(selection)
            self.robot_combo = QtWidgets.QComboBox()
            self.robot_combo.addItems(["mur620a", "mur620b", "mur620c", "mur620d"])
            local_robot = socket.gethostname().split(".")[0]
            self.robot_combo.setCurrentText(local_robot if local_robot in ("mur620a", "mur620b", "mur620c", "mur620d") else "mur620d")
            self.via_combo = QtWidgets.QComboBox()
            self.via_combo.addItem("Roboter-PC über SSH", "ssh")
            self.via_combo.addItem("Auf diesem PC", "local")
            if local_robot == self.robot_combo.currentText():
                self.via_combo.setCurrentIndex(1)
            self.mode_combo = QtWidgets.QComboBox()
            self.mode_combo.addItem("Laufender Betrieb", "operational")
            self.mode_combo.addItem("Vor dem Treiberstart", "preflight")
            self.domain_spin = QtWidgets.QSpinBox()
            self.domain_spin.setRange(0, 232)
            self.domain_spin.setValue(62)
            self.domain_spin.setToolTip(
                "Domain für diesen Beobachtungsprozess. Ändert keine Roboter- oder Shell-Konfiguration; "
                "die ursprüngliche Sitzungsumgebung wird getrennt geprüft.")
            self.namespace_edit = QtWidgets.QLineEdit()
            self.namespace_edit.setPlaceholderText("Standard: Robotername")
            self.host_edit = QtWidgets.QLineEdit()
            self.host_edit.setPlaceholderText("Standard: Robotername / SSH-Alias")
            self.workspace_edit = QtWidgets.QLineEdit("/home/rosmatch/colcon_ws")
            fields = [
                ("Roboter", self.robot_combo), ("Zugriff", self.via_combo),
                ("Prüfmodus", self.mode_combo), ("ROS-Domain", self.domain_spin),
                ("ROS-Namespace", self.namespace_edit), ("SSH-Ziel", self.host_edit),
                ("Workspace auf dem Ziel", self.workspace_edit),
            ]
            for index, (label, widget) in enumerate(fields):
                row, column = divmod(index, 4)
                layout.addWidget(QtWidgets.QLabel(label), row * 2, column)
                layout.addWidget(widget, row * 2 + 1, column)
            root.addWidget(selection)
            for combo in (self.robot_combo, self.via_combo, self.mode_combo):
                combo.currentIndexChanged.connect(self._selection_changed)
            self.domain_spin.valueChanged.connect(self._selection_changed)
            for field in (self.namespace_edit, self.host_edit, self.workspace_edit):
                field.textChanged.connect(self._selection_changed)

            controls = QtWidgets.QHBoxLayout()
            self.scan_button = QtWidgets.QPushButton("Diagnose starten")
            self.scan_button.setStyleSheet(
                "QPushButton {background: #285b80; color: white; font-weight: 600; padding: 8px 14px;}")
            self.watch_button = QtWidgets.QPushButton("Live beobachten")
            self.stop_button = QtWidgets.QPushButton("Stoppen")
            self.export_button = QtWidgets.QPushButton("Bericht exportieren …")
            self.scan_button.clicked.connect(lambda: self.start_scan(False))
            self.watch_button.clicked.connect(lambda: self.start_scan(True))
            self.stop_button.clicked.connect(self.stop_scan)
            self.export_button.clicked.connect(self._choose_export)
            for button in (self.scan_button, self.watch_button, self.stop_button):
                controls.addWidget(button)
            controls.addStretch()
            controls.addWidget(self.export_button)
            root.addLayout(controls)

            self.progress = QtWidgets.QProgressBar()
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.progress.setTextVisible(False)
            self.progress.setFixedHeight(5)
            root.addWidget(self.progress)
            self.summary_label = QtWidgets.QLabel("Bereit. Roboter und Prüfmodus auswählen.")
            self.summary_label.setWordWrap(True)
            root.addWidget(self.summary_label)

            cards = QtWidgets.QGridLayout()
            self.stat_labels = {}
            for index, (key, title) in enumerate(STAT_LABELS.items()):
                box = QtWidgets.QGroupBox(title)
                card_layout = QtWidgets.QVBoxLayout(box)
                value = QtWidgets.QLabel("Noch keine Daten")
                value.setWordWrap(True)
                value.setMinimumHeight(42)
                value.setStyleSheet("font-size: 14px; color: #20364d;")
                card_layout.addWidget(value)
                cards.addWidget(box, index // 3, index % 3)
                self.stat_labels[key] = value
            root.addLayout(cards)

            tabs = QtWidgets.QTabWidget()
            root.addWidget(tabs, 1)
            diagnosis = QtWidgets.QWidget()
            diagnosis_layout = QtWidgets.QVBoxLayout(diagnosis)
            filters = QtWidgets.QHBoxLayout()
            filters.addWidget(QtWidgets.QLabel("Fehlerbild"))
            self.symptom_combo = QtWidgets.QComboBox()
            self.symptom_combo.addItem("Alle Befunde", None)
            for article in self._articles:
                self.symptom_combo.addItem(article.get("title", article["id"]), article["id"])
            self.symptom_combo.currentIndexChanged.connect(self._render_results)
            filters.addWidget(self.symptom_combo, 1)
            self.problems_only = QtWidgets.QCheckBox("Nur Fehler, Hinweise und Unbekanntes")
            self.problems_only.toggled.connect(self._render_results)
            filters.addWidget(self.problems_only)
            diagnosis_layout.addLayout(filters)
            splitter = QtWidgets.QSplitter()
            self.results_tree = QtWidgets.QTreeWidget()
            self.results_tree.setHeaderLabels(["Bauteil / Prüfung", "Status", "Befund"])
            self.results_tree.setAlternatingRowColors(True)
            self.results_tree.setRootIsDecorated(True)
            self.results_tree.setColumnWidth(0, 185)
            self.results_tree.setColumnWidth(1, 90)
            self.results_tree.currentItemChanged.connect(self._show_result)
            self.details = QtWidgets.QTextBrowser()
            self.details.setOpenExternalLinks(False)
            self.details.setPlaceholderText("Einen Befund auswählen: Soll, Ist und nächste Schritte.")
            splitter.addWidget(self.results_tree)
            splitter.addWidget(self.details)
            splitter.setSizes([620, 440])
            diagnosis_layout.addWidget(splitter, 1)
            tabs.addTab(diagnosis, "Befunde")

            knowledge = QtWidgets.QWidget()
            knowledge_layout = QtWidgets.QVBoxLayout(knowledge)
            self.article_combo = QtWidgets.QComboBox()
            self.article_combo.currentIndexChanged.connect(self._show_article)
            knowledge_layout.addWidget(self.article_combo)
            self.article_view = QtWidgets.QTextBrowser()
            self.article_view.setOpenExternalLinks(False)
            knowledge_layout.addWidget(self.article_view, 1)
            self.manual_steps = QtWidgets.QListWidget()
            self.manual_steps.setMaximumHeight(130)
            self.manual_steps.itemChanged.connect(self._manual_step_changed)
            knowledge_layout.addWidget(self.manual_steps)
            knowledge_layout.addWidget(QtWidgets.QLabel(
                "Eigene Beobachtung zur Checkliste (wird ausdrücklich als Nutzerangabe exportiert):"))
            self.observation_edit = QtWidgets.QPlainTextEdit()
            self.observation_edit.setPlaceholderText(
                "Zum Beispiel: Ethernet-Stecker am linken UR geprüft; Link-LED leuchtet.")
            self.observation_edit.setMaximumHeight(70)
            knowledge_layout.addWidget(self.observation_edit)
            observation_controls = QtWidgets.QHBoxLayout()
            self.add_observation_button = QtWidgets.QPushButton("Beobachtung hinzufügen")
            self.add_observation_button.clicked.connect(self.add_observation)
            observation_controls.addWidget(self.add_observation_button)
            self.observation_count = QtWidgets.QLabel("Keine Nutzerangaben in dieser Sitzung")
            observation_controls.addWidget(self.observation_count, 1)
            knowledge_layout.addLayout(observation_controls)
            self.observation_list = QtWidgets.QListWidget()
            self.observation_list.setMaximumHeight(90)
            knowledge_layout.addWidget(self.observation_list)
            tabs.addTab(knowledge, "Wissen && Checklisten")

            self.process_log = QtWidgets.QPlainTextEdit()
            self.process_log.setReadOnly(True)
            self.process_log.setMaximumBlockCount(200)
            self.process_log.setMaximumHeight(90)
            self.process_log.setPlaceholderText("Hinweise zur laufenden Diagnose")
            self.process_log.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
            root.addWidget(self.process_log)
            self.setStyleSheet(self.styleSheet() + """
                QGroupBox {font-weight: 600; border: 1px solid #d1dbe4;
                    border-radius: 6px; margin-top: 8px; padding-top: 9px;}
                QGroupBox::title {subcontrol-origin: margin; left: 10px; padding: 0 4px;}
                QLineEdit, QComboBox {padding: 4px; min-height: 21px;}
                QPushButton {padding: 6px 10px;}
            """)

        def _populate_articles(self):
            for article in self._articles:
                self.article_combo.addItem(article.get("title", article["id"]), article["id"])
            self._show_article()

        def _show_article(self, *_args):
            article_id = self.article_combo.currentData()
            article = next((item for item in self._articles if item["id"] == article_id), None)
            if not article:
                self.article_view.setPlainText("Noch keine Wissenseinträge vorhanden.")
                return
            text = "# " + article.get("title", article_id) + "\n\n" + article.get("body", "")
            self.article_view.setMarkdown(text)
            self.manual_steps.blockSignals(True)
            self.manual_steps.clear()
            confirmed = {note.get("manual_step") for note in self._manual_observations
                         if note.get("knowledge_id") == article_id}
            for step in article.get("manual_steps", []):
                item = QtWidgets.QListWidgetItem(str(step), self.manual_steps)
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setData(QtCore.Qt.UserRole, str(step))
                item.setCheckState(QtCore.Qt.Checked if step in confirmed else QtCore.Qt.Unchecked)
            self.manual_steps.blockSignals(False)
            self.manual_steps.setVisible(self.manual_steps.count() > 0)

        def _manual_step_changed(self, item):
            article_id = self.article_combo.currentData()
            step = item.data(QtCore.Qt.UserRole)
            self._manual_observations = [note for note in self._manual_observations
                if not (note.get("knowledge_id") == article_id and note.get("manual_step") == step)]
            if item.checkState() == QtCore.Qt.Checked:
                self._manual_observations.append({
                    "robot": self.robot_combo.currentText(), "session_id": self._session_id,
                    "source": "user_reported", "knowledge_id": article_id,
                    "manual_step": step, "text": "Manuell bestätigt: " + step,
                    "observed_at": _utc_now(),
                })
            self._refresh_observations()

        def _refresh_observations(self):
            self.observation_list.clear()
            for note in self._manual_observations:
                self.observation_list.addItem("Nutzerangabe: " + note["text"])
            self.observation_count.setText(f"{len(self._manual_observations)} Nutzerangabe(n) in dieser Sitzung")

        def _selection_changed(self, *_args):
            self._cancel_process()
            self._session_id = uuid.uuid4().hex
            self._manual_observations = []
            self._last_report = None
            self.observation_list.clear()
            self.observation_edit.clear()
            self._show_article()
            self.observation_count.setText("Keine Nutzerangaben in dieser Sitzung")
            self.results_tree.clear()
            self.details.clear()
            self.process_log.clear()
            self._clear_stats()
            self.export_button.setEnabled(False)
            self.host_edit.setEnabled(self.via_combo.currentData() == "ssh")
            self.summary_label.setText("Auswahl geändert. Neue Diagnose starten.")

        def _clear_stats(self, text="Noch keine Daten"):
            for label in self.stat_labels.values():
                label.setText(text)
                label.setToolTip("")
                label.setStyleSheet("font-size: 14px; color: #586779;")

        def _set_running(self, running):
            self._running = running
            self.scan_button.setEnabled(not running)
            self.watch_button.setEnabled(not running)
            self.stop_button.setEnabled(running)
            self.export_button.setEnabled(self._last_report is not None)
            self.progress.setRange(0, 0 if running else 1)
            if not running:
                self.progress.setValue(1 if self._last_report else 0)

        def command_arguments(self, watch=False):
            args = ["-u", "-m", "match_mobile_diagnostics.cli", "watch" if watch else "scan",
                    "--robot", self.robot_combo.currentText(), "--via", self.via_combo.currentData(),
                    "--mode", self.mode_combo.currentData(), "--domain-id", str(self.domain_spin.value()),
                    "--format", "jsonl" if watch else "json"]
            for flag, value in (("--namespace", self.namespace_edit.text()),
                                ("--host", self.host_edit.text()),
                                ("--workspace", self.workspace_edit.text())):
                if value.strip() and (flag != "--host" or self.via_combo.currentData() == "ssh"):
                    args += [flag, value.strip()]
            return args

        def start_scan(self, watch=False):
            self._cancel_process()
            generation = self._generation
            self._last_report = None
            self._watching = watch
            self._report_received_at = None
            self._clear_stats("Warte auf Daten …")
            self.results_tree.clear()
            self.details.clear()
            self.process_log.clear()
            self.summary_label.setText("Live-Diagnose läuft …" if watch else "Diagnose läuft …")
            process = QtCore.QProcess(self)
            self._process = process
            state = {"stdout": "", "received": False, "protocol_error": False,
                     "decoder": codecs.getincrementaldecoder("utf-8")("replace")}
            process.setProcessChannelMode(QtCore.QProcess.SeparateChannels)
            process.readyReadStandardOutput.connect(
                lambda: self._read_output(process, generation, state, watch))
            process.readyReadStandardError.connect(
                lambda: self._read_errors(process, generation))
            process.finished.connect(
                lambda code, status: self._finished(process, generation, state, watch, code, status))
            process.errorOccurred.connect(
                lambda error: self._process_error(process, generation, error))
            self._set_running(True)
            # The offline CLI launcher adds its release to sys.path only in this process.
            # Pass that path to the Python child that performs the actual scan.
            environment = QtCore.QProcessEnvironment.systemEnvironment()
            package_root = str(Path(__file__).resolve().parent.parent)
            python_paths = [path for path in environment.value("PYTHONPATH").split(os.pathsep) if path]
            if package_root not in python_paths:
                environment.insert("PYTHONPATH", os.pathsep.join([package_root, *python_paths]))
            process.setProcessEnvironment(environment)
            process.start(sys.executable, self.command_arguments(watch))

        def _read_errors(self, process, generation):
            text = bytes(process.readAllStandardError()).decode("utf-8", "replace").strip()
            if generation == self._generation and text:
                self.process_log.appendPlainText(text[-16000:])

        def _read_output(self, process, generation, state, watch):
            raw = state["decoder"].decode(bytes(process.readAllStandardOutput()))
            if generation != self._generation:
                return
            state["stdout"] += raw
            if len(state["stdout"]) > 4_000_000:
                state["protocol_error"] = True
                self.summary_label.setText("Diagnoseausgabe zu groß oder unvollständig.")
                self._clear_stats("Ausgabe nicht verfügbar")
                self._cancel_process()
                return
            if watch:
                while "\n" in state["stdout"]:
                    line, state["stdout"] = state["stdout"].split("\n", 1)
                    if line.strip():
                        self._consume_report(line, generation, state)

        def _consume_report(self, text, generation, state):
            if generation != self._generation:
                return
            try:
                report = json.loads(text)
                self.apply_report(report, generation)
            except (ValueError, TypeError, KeyError) as exc:
                state["protocol_error"] = True
                self._last_report = None
                self.export_button.setEnabled(False)
                self._clear_stats("Ungültiger Bericht")
                self.results_tree.clear()
                self.details.clear()
                self.summary_label.setText("Diagnosebericht konnte nicht gelesen werden.")
                self.process_log.appendPlainText(str(exc))
                return
            state["received"] = True

        def _finished(self, process, generation, state, watch, code, status):
            self._read_output(process, generation, state, watch)
            self._read_errors(process, generation)
            if generation == self._generation:
                if state["stdout"].strip():
                    self._consume_report(state["stdout"], generation, state)
                self._process = None
                self._set_running(False)
                if not state["received"] or status == QtCore.QProcess.CrashExit:
                    self._clear_stats("Verbindung unterbrochen / keine Daten")
                    self.summary_label.setText(
                        "Diagnose abgebrochen oder Ziel nicht erreichbar. Hinweise unten beachten.")
                elif watch:
                    self._clear_stats("Live-Verbindung beendet")
                    self.summary_label.setText("Live-Verbindung beendet. Letzter Bericht bleibt exportierbar.")
                self.process_log.appendPlainText(f"Diagnoseprozess beendet (Exit {code}).")
            process.deleteLater()

        def _process_error(self, process, generation, error):
            if generation != self._generation:
                return
            self.process_log.appendPlainText(process.errorString())
            if error == QtCore.QProcess.FailedToStart:
                self._clear_stats("Diagnose konnte nicht starten")
                self.summary_label.setText("Diagnoseprozess konnte nicht gestartet werden.")
                self._process = None
                self._set_running(False)
                process.deleteLater()

        def _cancel_process(self):
            self._generation += 1
            process, self._process = self._process, None
            if process is not None and process.state() != QtCore.QProcess.NotRunning:
                process.terminate()

                def kill_if_running():
                    try:
                        if process.state() != QtCore.QProcess.NotRunning:
                            process.kill()
                    except RuntimeError:
                        pass  # finished already deleted the QObject

                QtCore.QTimer.singleShot(1000, kill_if_running)
            self._set_running(False)

        def stop_scan(self):
            self._cancel_process()
            self._clear_stats("Beobachtung gestoppt")
            self.summary_label.setText("Diagnose gestoppt. Vorhandener Bericht bleibt exportierbar.")

        def apply_report(self, report, generation=None):
            if generation is not None and generation != self._generation:
                return
            if not isinstance(report, dict) or report.get("schema_version") != 1:
                raise ValueError("Unbekanntes Berichtsformat; CLI und GUI müssen kompatibel sein.")
            if report.get("robot") != self.robot_combo.currentText():
                raise ValueError("Bericht gehört nicht zum ausgewählten Roboter.")
            if not isinstance(report.get("results"), list) or not isinstance(report.get("stats", {}), dict):
                raise ValueError("Bericht enthält keine gültigen Befunde oder Statuswerte.")
            self._last_report = copy.deepcopy(report)
            self._report_received_at = time.monotonic()
            complete = "" if report.get("complete") else " · Prüfung unvollständig"
            self.summary_label.setText(
                f"{report.get('summary', 'Diagnose abgeschlossen')}{complete}\n"
                f"{report.get('robot')} · {report.get('checked_at', 'Zeit unbekannt')}")
            for key, label in self.stat_labels.items():
                stat = report.get("stats", {}).get(key)
                if not isinstance(stat, dict):
                    label.setText("Nicht erfasst")
                    label.setToolTip("")
                    continue
                lines = [_display(stat.get("text", "Keine Daten"))]
                if key.startswith("ur_"):
                    program = stat.get("loaded_program")
                    if program and str(program) not in lines[0]:
                        lines.append("Programm: " + str(program))
                    if isinstance(stat.get("remote"), bool):
                        lines.append("Remote" if stat["remote"] else "Lokaler Modus")
                    controllers = stat.get("motion_controllers", stat.get("controllers"))
                    if controllers is not None:
                        available = stat.get("controllers_available", stat.get("ros", {}).get("controllers_available", True))
                        names = [str(value) for value in controllers]
                        lines.append("ROS: " + ((", ".join(names) if names else "kein aktiver Bewegungscontroller") if available else "Controller nicht prüfbar"))
                if stat.get("status") in ("unknown", "warn", "fail"):
                    lines.append(STATUS_LABELS[stat["status"]])
                label.setText("\n".join(lines))
                label.setStyleSheet("font-size: 14px; color: " +
                    STATUS_COLORS.get(stat.get("status"), "#20364d") + ";")
                age = stat.get("age_seconds")
                age_text = f"{age:.1f} s" if isinstance(age, (int, float)) else "unbekannt"
                label.setToolTip(
                    f"Quelle: {_display(stat.get('source'))}\n"
                    f"Beobachtet: {_display(stat.get('observed_at'))}\nAlter: {age_text}")
            self.export_button.setEnabled(True)
            self._render_results()

        def _refresh_freshness(self):
            if not self._running or not self._watching or not self._last_report or self._report_received_at is None:
                return
            elapsed = time.monotonic() - self._report_received_at
            limits = {"mir_battery": 10.0, "mur_battery": 5.0,
                      "ur_l": 6.0, "ur_r": 6.0, "lift_l": 2.0, "lift_r": 2.0, "clock": 60.0}
            for key, label in self.stat_labels.items():
                stat = self._last_report.get("stats", {}).get(key, {})
                age = stat.get("age_seconds") if isinstance(stat, dict) else None
                if isinstance(age, (int, float)) and age + elapsed > limits[key]:
                    label.setText("Veraltet · zuletzt: " + _display(stat.get("text")))
                    label.setStyleSheet("font-size: 14px; color: #586779;")
                    label.setToolTip(f"Seit {age + elapsed:.1f} s keine aktuellere Beobachtung in der GUI.")

        def _render_results(self, *_args):
            self.results_tree.clear()
            self.details.clear()
            if not self._last_report:
                return
            article_id = self.symptom_combo.currentData()
            article = next((item for item in self._articles if item["id"] == article_id), {})
            check_ids = {value for value in article.get("checks", []) if isinstance(value, str)}
            groups = {}
            first = None
            for result in self._last_report.get("results", []):
                if not isinstance(result, dict):
                    continue
                if article_id and result.get("knowledge_id") != article_id and not any(
                        str(result.get("id", "")).startswith(prefix) for prefix in check_ids):
                    continue
                status = result.get("status", "unknown")
                if self.problems_only.isChecked() and status in ("pass", "not_applicable"):
                    continue
                component = result.get("component", "Allgemein")
                if component not in groups:
                    groups[component] = QtWidgets.QTreeWidgetItem(self.results_tree, [component])
                    groups[component].setExpanded(True)
                item = QtWidgets.QTreeWidgetItem(groups[component], [
                    result.get("id", "Prüfung"), STATUS_LABELS.get(status, status), result.get("summary", "")])
                item.setData(0, QtCore.Qt.UserRole, result)
                item.setForeground(1, QtGui.QBrush(QtGui.QColor(STATUS_COLORS.get(status, "#586779"))))
                if first is None:
                    first = item
            if first is not None:
                self.results_tree.setCurrentItem(first)
            else:
                self.details.setPlainText("Keine Befunde für diesen Filter.")

        def _show_result(self, item, _previous=None):
            result = item.data(0, QtCore.Qt.UserRole) if item is not None else None
            if not result:
                self.details.clear()
                return
            parts = [f"<h3>{_escape(result.get('summary'))}</h3>"]
            for title, key in (("Soll", "expected"), ("Ist", "actual"), ("Quelle", "source"),
                               ("Beobachtet", "observed_at"), ("Alter in Sekunden", "age_seconds")):
                parts.append(f"<p><b>{title}</b><br>{_escape(result.get(key))}</p>")
            for title, key in (("Belege", "evidence"), ("Mögliche Ursachen", "causes"),
                               ("Nächste Schritte", "next_steps")):
                entries = result.get(key, [])
                if entries:
                    parts.append(f"<p><b>{title}</b></p><ul>" + "".join(
                        f"<li>{_escape(entry)}</li>" for entry in entries) + "</ul>")
            self.details.setHtml("".join(parts))

        def add_observation(self):
            text = self.observation_edit.toPlainText().strip()
            if not text:
                return
            observation = {
                "robot": self.robot_combo.currentText(), "session_id": self._session_id,
                "source": "user_reported", "knowledge_id": self.article_combo.currentData(),
                "text": text, "observed_at": _utc_now(),
            }
            self._manual_observations.append(observation)
            self.observation_edit.clear()
            self._refresh_observations()

        def export_data(self):
            if self._last_report is None:
                raise ValueError("Zuerst einen Diagnosebericht erstellen.")
            report = copy.deepcopy(self._last_report)
            report["gui_session_id"] = self._session_id
            report["manual_observations"] = [copy.deepcopy(item) for item in self._manual_observations
                if item["robot"] == report["robot"] and item["session_id"] == self._session_id]
            from .models import redact
            return redact(report)

        def export_report(self, path):
            report = self.export_data()
            path = Path(path)
            if path.suffix.lower() == ".md":
                from .reports import markdown_report
                content = markdown_report(report)
            else:
                content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            path.write_text(content, encoding="utf-8")

        def _choose_export(self):
            filename, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
                self, "Diagnosebericht exportieren", f"{self.robot_combo.currentText()}_diagnose.json",
                "JSON (*.json);;Markdown (*.md)")
            if not filename:
                return
            path = Path(filename)
            if not path.suffix:
                path = path.with_suffix(".md" if "Markdown" in selected_filter else ".json")
            try:
                self.export_report(path)
            except (OSError, ValueError) as exc:
                QtWidgets.QMessageBox.warning(self, "Export fehlgeschlagen", str(exc))
                return
            self.process_log.appendPlainText(f"Bericht gespeichert: {path}")

        def closeEvent(self, event):
            self._cancel_process()
            super().closeEvent(event)


def main(argv=None):
    if QtWidgets is None:
        print("Für die GUI wird PyQt5 benötigt. Die Diagnose-CLI funktioniert auch ohne Qt.", file=sys.stderr)
        return 2
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv or sys.argv)
    window = DiagnosticWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
