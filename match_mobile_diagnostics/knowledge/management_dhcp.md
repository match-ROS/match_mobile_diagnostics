# Roboter-PC über WLAN oder Repeater nicht erreichbar

Die versionierten Management-Adressen sind A 10.145.8.60, B .54, C .41 und D .85, jeweils /24. Die aktiven Schnittstellen unterscheiden sich zwischen D und A–C.

Bestätigte Fälle (September 2026): Der RE800BE-MAC-Proxy verhinderte eine funktionierende DHCP-Zuweisung, obwohl WLAN verbunden war. Direkte Verkabelung funktionierte; eine statische Management-Adresse stellte die Verbindung wieder her. Ein Firmwareupdate allein löste das Problem nicht. Ein anderer Host hatte trotz eingetragener statischer Adresse noch ipv4.method=auto und verlor die Adresse nach dem DHCP-Timeout.

Eine gute WLAN-Signalstärke beweist daher weder eine erfolgreiche DHCP-Zuweisung noch bidirektionale IP-Erreichbarkeit. Änderungen an Management-IP oder Verbindung nur über einen erreichbaren lokalen Zugang durchführen; das Diagnosewerkzeug führt diese Änderungen nicht aus.

## Manuelle Prüfschritte

- [ ] Management-Kabel und Repeater-Verbindung prüfen.
- [ ] Aktive Management-Adresse mit dem Sollprofil vergleichen.
- [ ] NetworkManager-Methode und DHCP-Meldungen prüfen.

## Beleggrundlage

Fachliche Zusammenfassung lokaler MuR-Diagnosesitzungen (Juni–September 2026) und aktueller Host-/Launch-Konfigurationen in match_mobile_robotics_jazzy. Rohchats und Zugangsdaten werden nicht veröffentlicht. Nicht bestätigte Ursachen sind im Text ausdrücklich als Vermutung gekennzeichnet.
