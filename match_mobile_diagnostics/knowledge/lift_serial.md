# Hubsäule fehlt oder Kommunikation ist unterbrochen

Nur C und D haben Hubsäulen. Historisch fehlten serielle Rechte; später existierten auf C ttyUSB0 und ttyUSB2 statt ttyUSB0 und ttyUSB1. Stabile /dev/serial/by-id-Pfade vermeiden wechselnde Nummern. Die C-Zuordnung im vorhandenen Hostprofil ist trotzdem noch als vorläufig markiert und muss physisch bestätigt werden.

Der bisherige Treiber veröffentlicht zwischengespeicherte State-Nachrichten auch bei fehlgeschlagenem Hardwareabruf. Die neue DiagnosticArray-Ergänzung nennt daher last_success_age_sec und hardware_comm_ok. Ohne diesen Zusatz ist tatsächliche Messfrische nicht sicher nachweisbar. Historische Error-Einträge sind kein Beweis für einen gerade aktiven Fehler.

Die Höhenanzeige verwendet die ersten beiden gültigen Positionswerte und 3225 Ticks pro Meter. Sie öffnet keinen seriellen Port. Bei einem früheren Jog-Problem war der Befehl kleiner als die eingestellte Toleranz; eine Treiberänderung war gebaut, der laufende Prozess musste sie erst laden. Daraus folgt keine allgemeine mechanische Fehlerdiagnose.

## Manuelle Prüfschritte

- [ ] USB-Adapter und serielle Kabel links/rechts verfolgen.
- [ ] Tatsächliche by-id-Geräte und Zugriffsrechte prüfen.
- [ ] Aktuelle Kommunikationsdiagnose und Fehlerhistorie getrennt betrachten.

## Beleggrundlage

Fachliche Zusammenfassung lokaler MuR-Diagnosesitzungen (Juni–September 2026) und aktueller Host-/Launch-Konfigurationen in match_mobile_robotics_jazzy. Rohchats und Zugangsdaten werden nicht veröffentlicht. Nicht bestätigte Ursachen sind im Text ausdrücklich als Vermutung gekennzeichnet.
