# Aufbaubatterie fehlt oder ist veraltet

BMS-IDs: A 0x0340, B 0x0140, C 0x0240, D 0x0440. MiR-Batterie und Aufbaubatterie sind verschiedene Datenquellen. MiR BatteryState.percentage ist ein Anteil von 0 bis 1, bms_status/SOC ein Prozentwert. Negative oder nicht endliche Werte bedeuten keine gültige Ladungsmessung.

Bestätigte Fälle: Adapter nicht enumeriert (kein can0), vorhandenes can0 im Zustand DOWN sowie fehlendes python-can. Auf C war im September 2026 die BMS-ID bereits richtig; CAN DOWN war die Ursache. Ein nichtinteraktiver sudo-Aufruf hatte die Aktivierung verhindert.

CAN ERROR-ACTIVE ist der normale aktive Fehlerzustand und allein kein Fehler. BUS-OFF oder fehlende Kommunikation sind anders zu behandeln. Der Helper liest vorhandene ROS-Messwerte und Betriebssystemzustände; er startet keinen BMS-Treiber und sendet keine eigenen CAN-Abfragen.

## Manuelle Prüfschritte

- [ ] USB-CAN-Adapter und Verkabelung prüfen.
- [ ] can0 vorhanden und aktiv mit 250000 bit/s prüfen.
- [ ] BMS-ID mit dem ausgewählten Roboter vergleichen.

## Beleggrundlage

Fachliche Zusammenfassung lokaler MuR-Diagnosesitzungen (Juni–September 2026) und aktueller Host-/Launch-Konfigurationen in match_mobile_robotics_jazzy. Rohchats und Zugangsdaten werden nicht veröffentlicht. Nicht bestätigte Ursachen sind im Text ausdrücklich als Vermutung gekennzeichnet.
