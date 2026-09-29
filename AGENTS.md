# Arbeit mit match_mobile_diagnostics

Dieses Repository unterstützt Hardware-, Netzwerk-, Betriebssystem- und MuR-Treiberdiagnosen. Untersuche Anwendungslogik nur in einem separat beauftragten Arbeitsschritt. Das Diagnoseprogramm selbst bleibt lesend und kann ohne ROS oder GUI-Pakete starten.

## Diagnoseablauf

1. Wähle mit `--robot` die physische Maschine und verwende vom Laptop `--via ssh`. `--host` ist nur das Verbindungsziel, `--namespace` nur der ROS-Namespace und `--domain-id` die ausdrücklich gewählte Beobachtungsdomain (Profilstandard 62). Vertausche diese Identitäten nicht.
2. Erzeuge einen Bericht mit `scan --format json`; für Verlauf `watch --format jsonl`. Verarbeite das versionierte JSON und stabile Check-IDs, nicht die Formulierung menschlicher Meldungen.
3. Prüfe `complete`, alle `counts` und jeden relevanten `results[].status`. `unknown` ist fehlende Evidenz, kein Erfolg und keine bestätigte Hardwareursache. `not_applicable` bedeutet fehlende Anwendbarkeit auf das Profil.
4. Nutze Quelle, Beobachtungszeit, Alter und Belege. Verfolge ausgefallene Voraussetzungen zuerst. Ein alter Logtreffer, ein laufendes UR-Programm oder ein sichtbares ROS-Topic allein belegt keine aktuelle Betriebsbereitschaft.
5. Lade bei Bedarf `knowledge show ARTIKEL --format json`. Manuelle Prüfschritte werden als `source=user_reported` protokolliert und niemals in automatische `pass`-Ergebnisse umgewandelt.
6. Berichte die festgestellte Abweichung, die tragenden Belege, verbleibende Unbekannte und konkrete nächste Schritte. Exportiere nur aufgabenrelevante, bereinigte Daten.

Beispiel:

```bash
python3 -m match_mobile_diagnostics.cli scan --robot mur620c --via ssh --mode preflight --format json
python3 -m match_mobile_diagnostics.cli knowledge show network_address --format json
```

## Technische Grenzen

- Diagnose niemals um Reparaturaktionen erweitern: kein Hardwarestart, kein Controllerwechsel, kein Dashboard-`play`/`stop`/Reset, keine Netzwerkkonfiguration, kein `sudo`, keine Installation oder automatische Patchanwendung.
- Verwende explizit zugelassene Statusabfragen. Öffne Lift-Serialports nicht als zusätzliche Diagnoseinstanz; ein Treiber kann beim Öffnen/Starten bereits Aktivierungs- und Stopbefehle senden. Sende keine zusätzlichen CAN-Anfragen.
- Die Engine wählt für den isolierten Beobachtungsprozess die explizite `--domain-id` bzw. den Profilstandard 62. Das ist Beobachtungskontext, keine Reparatur: keine Umgebungsdateien und keine laufenden Prozesse verändern. Ursprüngliche Sitzungsdomain, Beobachtungsdomain und Domains laufender Treiber getrennt erfassen. Eine nicht gesetzte SSH-Sitzungsdomain allein ist kein Roboterfehler. Die direkte Collector-API ohne Domainparameter behält die tatsächliche Aufrufumgebung bei.
- Änderungen an Treibern oder Konfigurationen sind eigene Implementierungs-/Installationsaufgaben. Der Ewellix-Installer liegt im Jazzy-Repository und wird durch normale Scans niemals aufgerufen.
- Fehlende ROS-/Qt-Imports dürfen den Standardbibliothekskern und die Offline-Wissensbasis nicht blockieren. Halte ROS-Erfassung und GUI-Prozess voneinander getrennt.
- Bewahre Signalqualität: UR-Modi senden nur bei Änderungen mit transient-local QoS. Lift-`state` kann zwischengespeichert sein. Nutze Kommunikationsevidenz und signalabhängige Alterung statt eines pauschalen Topic-Timeouts.

## Änderungen und Tests

Pflege Profile mit `profile_version` sowie Herkunft, Revision, Datum und Bestätigungsstatus. Bestehende Sollwerte nicht aus einer einzelnen fehlerhaften Laufzeitmessung überschreiben. Die mur620c-Liftportzuordnung bleibt bis zur physischen Bestätigung vorläufig.

Für neue Fehlerbilder ergänze maschinenlesbare Check-Zuordnung, deutsche Erklärung und manuelle Schritte in der Wissensbasis. Neue automatisierte Regeln benötigen aussagekräftige Tests für den Fehlerfall, fehlende Voraussetzungen und widersprechende oder veraltete Belege. Behalte `schema_version` und CLI-Vertrag kompatibel; ändere das Schema nur ausdrücklich versioniert.

```bash
python3 -m pytest tests -q
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_gui.py -q
```

Keine Roboterverbindung für Unit-Tests voraussetzen. Live-Abnahmen werden separat in `docs/acceptance.md` mit Zeitpunkt, Roboter, Tool-/Treiberrevision und Ergebnis dokumentiert. Simulierte Tests niemals als Live-Nachweis bezeichnen.

Keine Rohlogs, exportierten Diagnoseberichte, Chattranskripte, Zugangsdaten oder lokale SSH-Konfigurationen committen. Nutze abstrahierte Fehlerbeispiele und synthetische Testdaten. Auch bei vorhandener Redaktionsfunktion Exporte vor einer Veröffentlichung prüfen.
