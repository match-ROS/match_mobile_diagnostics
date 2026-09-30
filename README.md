# match_mobile_diagnostics

Eine interaktive Diagnosehilfe für MuR620a–d: Sie vergleicht Hardware-, Netzwerk- und Treiberzustände mit dokumentierten Sollwerten und erklärt Abweichungen. Dieselben Befunde stehen in einer kleinen GUI, als JSON für Agenten und als Markdown-Bericht bereit. Eine lokale Wissensbasis ergänzt automatische Prüfungen durch nachvollziehbare Checklisten.

Die Diagnose liest Zustände. Sie startet keine Hardware, bewegt keine Achsen, setzt keine Sicherheitszustände zurück und ändert weder Netzwerk noch Controller oder Betriebssystem. Anwendungsprogramme werden nicht untersucht; bekannte Integrationsfehler des MuR-Treiberstacks gehören zum Umfang.

## Installation

Python 3.10 oder neuer genügt für CLI, Netzwerkprüfungen und Wissensbasis. Der Kern benötigt keine Python-Pakete außerhalb der Standardbibliothek. PyQt5 ist nur für die GUI nötig; ROS 2 Jazzy und die jeweiligen Nachrichtenpakete sind nur auf dem Rechner nötig, der ROS-Daten erfasst.

Für die Oberfläche auf einem Ubuntu-Rechner mit systemseitig installiertem `python3-pyqt5`:

```bash
git clone https://github.com/match-ROS/match_mobile_diagnostics.git
cd match_mobile_diagnostics
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -e .
python -m match_mobile_diagnostics.cli gui
```

`--system-site-packages` macht das vorhandene System-PyQt5 in der Umgebung sichtbar. Für reine CLI-Nutzung kann die virtuelle Umgebung ohne diese Option erstellt werden. Das Tool installiert fehlende Abhängigkeiten nicht selbst.

Auf jedem Roboter-PC wird der Diagnosekern separat eingerichtet. Empfohlen ist eine eigene virtuelle Umgebung mit festem CLI-Einstieg. Voraussetzung sind das geklonte Repository, `python3-venv` und die vorhandenen Build-Werkzeuge `setuptools`/`wheel`:

```bash
python3 -m venv --system-site-packages "$HOME/.local/share/mur-diagnostics/venv"
"$HOME/.local/share/mur-diagnostics/venv/bin/python" -m pip install --no-build-isolation --no-deps /home/rosmatch/colcon_ws/src/match_mobile_diagnostics
mkdir -p "$HOME/.local/bin"
ln -s "$HOME/.local/share/mur-diagnostics/venv/bin/mur-diagnostics" "$HOME/.local/bin/mur-diagnostics"
"$HOME/.local/bin/mur-diagnostics" --version
```

Der Symlink-Befehl überschreibt keine vorhandene Installation. Existiert das Ziel bereits, zuerst die bestehende Installation prüfen und bei Bedarf gezielt aktualisieren. SSH erkennt `~/.local/bin/mur-diagnostics` ausdrücklich, auch wenn der nichtinteraktive PATH dieses Verzeichnis nicht enthält. Der Laptop und die Roboter sollen dieselbe Toolversion verwenden.

Für Roboter-PCs ohne pip oder virtuelle Umgebung gibt es einen vollständig offline nutzbaren Standardbibliotheks-Installer. Dies ist auch der verwendete Weg für die erste Bereitstellung auf den Robotern:

```bash
cd /home/rosmatch/colcon_ws/src/match_mobile_diagnostics
python3 scripts/install_cli.py
"$HOME/.local/bin/mur-diagnostics" --version
```

`--source REPOSITORY`, `--prefix VERZEICHNIS` und `--bin-dir VERZEICHNIS` erlauben andere lokale Pfade. Der Installer legt ausschließlich Paketcode, Profile, Wissensartikel und Lizenz unter `~/.local/share/mur-diagnostics/releases/<version>-<inhalts-hash>` ab und aktualisiert den verwalteten CLI-Symlink atomar. Eine unveränderte Installation ist wiederholbar; fremde vorhandene CLI-Dateien oder Symlinks werden nicht überschrieben. Bytecode-Dateien werden auch in gestarteten Kindprozessen unterdrückt, damit das Release unverändert bleibt. Diese explizite Installation wird niemals durch einen Diagnoselauf ausgelöst.

Alternativ wird das Paket im vorhandenen Colcon-Workspace gebaut:

```bash
cd /home/rosmatch/colcon_ws/src
git clone https://github.com/match-ROS/match_mobile_diagnostics.git
cd /home/rosmatch/colcon_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-select match_mobile_diagnostics
source install/setup.bash
ros2 run match_mobile_diagnostics mur-diagnostics --version
```

Das Colcon-Executable liegt paketüblich unter `install/match_mobile_diagnostics/lib/match_mobile_diagnostics/mur-diagnostics`, bei einem zusammengeführten Installationsverzeichnis unter `install/lib/match_mobile_diagnostics/mur-diagnostics`. `ros2 run` funktioniert nach dem Sourcen. Der SSH-Aufrufer erkennt diese beiden Installationspfade und sourct für diesen Einstieg das Workspace-Setup. Alternativ bleibt `python3 -m match_mobile_diagnostics.cli` nutzbar, sobald das Paket im Python-Pfad liegt.

Die ROS-Erfassung nutzt den System-Python und vorhandene Jazzy-/Workspace-Nachrichten, insbesondere `sensor_msgs`, `std_msgs`, `controller_manager_msgs`, `ur_dashboard_msgs`, `ur_msgs`, `diagnostic_msgs` und bei Hubsäulen `ewellix_interfaces`. Fehlende Komponenten werden als nicht prüfbar ausgewiesen; Host- und Dashboard-Prüfungen bleiben verfügbar.

## Erste Diagnose

Vom Laptop aus läuft die interne Prüfung auf dem gewählten Roboter-PC. So werden die privaten Geräteadressen und Rückverbindungen aus der richtigen Netzwerkumgebung geprüft:

```bash
python -m match_mobile_diagnostics.cli scan --robot mur620d --via ssh --format markdown
python -m match_mobile_diagnostics.cli scan --robot mur620c --via ssh --mode preflight --format json --output mur620c_preflight.json
python -m match_mobile_diagnostics.cli watch --robot mur620d --via ssh --format jsonl
```

Vorausgesetzt werden vorhandene SSH-Schlüssel, ein verifizierter Eintrag in `known_hosts` und der installierte Diagnosekern auf dem Roboter-PC. Passwortabfragen und automatische Installation gehören nicht zum Diagnoseablauf. `--host user@host` oder ein SSH-Alias überschreibt das Verbindungsziel; der physische Zielhostname wird weiterhin gegen `--robot` geprüft.

Direkt auf dem Roboter-PC:

```bash
source /opt/ros/jazzy/setup.bash
source /home/rosmatch/colcon_ws/install/setup.bash
python3 -m match_mobile_diagnostics.cli scan --robot mur620d --via local --domain-id 62 --mode operational --format json
```

Domain **62** ist der Profilstandard für die Beobachtung. `--domain-id` und das GUI-Feld „ROS-Domain“ wählen ausdrücklich die Domain des Diagnoseprozesses; sie ändern keine Umgebungsdateien und keine laufenden Treiber. Die ursprüngliche Sitzungsumgebung, die gewählte Beobachtungsdomain und die soweit lesbar ermittelten Domains laufender Treiber werden getrennt berichtet. Eine SSH-Sitzung ohne `ROS_DOMAIN_ID` (effektiv 0) bei laufenden Treibern in Domain 62 ist allein kein Roboterfehler. Entscheidend ist, ob die Beobachtung die richtigen Treiber erreicht. Bei SSH wird die Auswahl an den Beobachtungsprozess auf dem Roboter-PC übertragen. Die ROS-Erfassung sourct vorhandene Jazzy- und Workspace-Setups, aber eine fehlende Setup-Datei verhindert nicht die übrigen Prüfungen.

| Option | Bedeutung |
| --- | --- |
| `--robot mur620a\|mur620b\|mur620c\|mur620d` | Physischer Roboter und Sollprofil; erforderlich. |
| `--via local\|ssh` | Ausführungsort; CLI-Standard ist `local`, GUI-Standard SSH. |
| `--host ALIAS` | Abweichendes SSH-Ziel. |
| `--domain-id ZAHL` | ROS-Domain des Beobachtungsprozesses, 0–232; ohne Angabe Profilstandard 62. |
| `--namespace NAME` | Expliziter ROS-Namespace; sonst anhand vorhandener Schnittstellen ermitteln. |
| `--workspace PFAD` | Workspace auf dem Ziel-PC; Standard aus dem Profil. |
| `--mode operational\|preflight` | Betriebsbereitschaft oder Voraussetzungen vor dem Treiberstart. |
| `--duration SEKUNDEN` | Erstes ROS-Beobachtungsfenster; Standard 10 s, Bereich 0,1–60 s. |
| `--format json\|jsonl\|markdown` | Einzelscan standardmäßig Markdown, Watch standardmäßig JSONL. Watch akzeptiert kein mehrzeiliges JSON. |
| `--interval SEKUNDEN` | Nur Watch: angestrebter Abstand, Standard 2 s; langsame Prüfungen können länger dauern. |
| `--output DATEI` | Bericht zusätzlich lokal speichern; Watch ersetzt die Datei durch den neuesten vollständigen Snapshot. |

Roboteridentität und ROS-Namespace sind getrennt. Beispielsweise kann ein physischer `mur620c` unter `/mur620` veröffentlichen. Bei Mehrdeutigkeit den Namespace ausdrücklich wählen; das Profil darf dadurch nicht auf einen anderen Roboter wechseln.

`preflight` berücksichtigt, dass Treiber oder UR-Antriebe noch nicht gestartet sind. Ein erreichbarer UR in `POWER_OFF` kann dabei ein zulässiger Ausgangszustand sein. Ein nicht prüfbarer Zustand wird trotzdem nicht als bestanden gewertet.

## Oberfläche, Wissen und Agenten

```bash
python -m match_mobile_diagnostics.cli gui
python -m match_mobile_diagnostics.cli knowledge list
python -m match_mobile_diagnostics.cli knowledge show ur_reverse
python -m match_mobile_diagnostics.cli knowledge list --format json
```

Auf einem MuR-PC wählt die GUI diesen Roboter und lokalen Zugriff vor; auf anderen Rechnern startet sie mit SSH-Zugriff. Die GUI zeigt getrennte MiR-/MuR-Akkus, beide URs mit Programm und ROS-Controllern sowie die Hubsäulen. Befunde enthalten Soll, Ist, Quelle, Zeitpunkt, mögliche Ursachen und nächste Schritte. Filter ändern nur die Darstellung. Bestätigte manuelle Prüfpunkte und Freitextbeobachtungen bleiben **Nutzerangaben**; sie ändern keinen automatischen Prüferfolg. Export und Auswahlwechsel berücksichtigen Roboter und Sitzung, alte Live-Werte werden als veraltet markiert.

Für Agenten ist JSON die verbindliche Schnittstelle: zuerst einen Scan erstellen, dann `results[].status`, `expected`, `actual`, `evidence`, `causes`, `next_steps` und `knowledge_id` auswerten. Keine Diagnose aus Farben oder frei formuliertem Terminaltext ableiten. [AGENTS.md](AGENTS.md) beschreibt den Arbeitsablauf.

| Status | Bedeutung |
| --- | --- |
| `pass` | Prüfung mit vorhandenen Belegen bestanden. |
| `warn` | Hinweis oder Abweichung mit weiterem Prüfbedarf. |
| `fail` | Bestätigte Abweichung vom Soll. |
| `unknown` | Voraussetzungen oder hinreichende aktuelle Belege fehlen. |
| `not_applicable` | Prüfung trifft auf dieses Profil nicht zu. |

CLI-Exitcodes: **0** vollständig und ohne Befunde, **1** Fehler oder Hinweise, **2** unvollständig ohne Fehler/Hinweise, **3** Aufruf-/interner Fehler, **130** abgebrochen. Bei gleichzeitigem Fehler und fehlenden Prüfungen gilt Exit 1; `complete` und `counts.unknown` bleiben zusätzlich relevant. „Alle Checks bestanden“ bezieht sich ausschließlich auf die vorgesehenen automatischen Prüfungen.

## Zeitsynchronisation

Die Zeitprüfung liest die Chrony-Konfiguration und den aktuellen Status auf dem Roboter-PC. Bei SSH-Zugriff prüft sie zusätzlich den GUI-Rechner und misst eine begrenzte Uhrdifferenz per SSH. Erwartet wird der vom Nutzer genannte Chrony-Server `10.145.8.50`; sein Betrieb wurde nicht als gesunder Ausgangszustand vorausgesetzt. Ohne `chronyc` werden Konfiguration und tatsächliche Synchronisation getrennt bewertet. Die GUI zeigt das Ergebnis in „Zeitsynchronisation“ und die Belege unter „Befunde“. [Checkliste](match_mobile_diagnostics/knowledge/clock_sync.md).

## Sollprofile und Hubsäulen

Versionierte Profile enthalten Adressen, Schnittstellen, Rückverbindungsports und Hardwaremerkmale. Jeder Sollwert hat eine Herkunft, Quellrevision, Erfassungsdatum und Bestätigungsstatus. Diese Angaben beschreiben konfigurierte Erwartungen, keine pauschal live verifizierte Bestandsaufnahme. Besonders die Links-/Rechts-Zuordnung der USB-Adapter an **mur620c ist vorläufig** und muss am Roboter bestätigt werden.

Frische Ewellix-`state`-Nachrichten allein beweisen keine frischen Hardwareantworten: Der ursprüngliche Treiber kann zwischengespeicherte Werte erneut senden. Für eine bestätigte Lift-Kommunikationsdiagnose gibt es einen separat versionierten Patch im benachbarten Jazzy-Repository: [Ewellix-Patch und Installation](../match_mobile_robotics_jazzy/patches/README.md). Er benötigt explizites `--apply`, einen eigenen Build und einen separat abgestimmten Treiberneustart. Der normale Diagnoseablauf führt keinen dieser Schritte aus. Ohne die zusätzliche Schnittstelle bleibt die Hardwarefrische unbekannt.

Berichte sind lokale Arbeitsartefakte. Rohlogs, Chatverläufe, Zugangsdaten oder ungeprüfte Exporte gehören nicht in das Repository oder öffentliche Tickets. Die Wissensbasis enthält abstrahierte Fehlerbilder; historische Logtreffer werden nicht ohne verlässlichen Zeitbezug als aktueller Fehler ausgegeben.

## Entwicklung und Nachweis

```bash
python3 -m pytest tests -q
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_gui.py -q
```

Tests verwenden simulierte Beobachtungen und lokale Prozesse; sie benötigen keine erreichbaren Roboter. [Architektur](docs/architecture.md) beschreibt Datenfluss und Schnittstellen. Die getrennte [Abnahmeübersicht](docs/acceptance.md) dokumentiert, welche Live-Prüfungen noch ausstehen.
