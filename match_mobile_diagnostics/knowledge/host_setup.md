# Host vorbereitet, aber Treiber startet nicht

SSH-Erreichbarkeit beweist nicht, dass ROS und die Treiber installiert sind. Auf A/C fehlten in früheren Sitzungen unter anderem Jazzy, Build-Werkzeuge, python-can oder ein gebauter Workspace. Übersprungene Hardwareprüfungen dürfen dann keinen gesunden Roboter melden.

Installiert und aktiv sind verschiedene Zustände: Ein installierter RT-Kernel ist erst nach entsprechendem Boot aktiv; Gruppenänderungen gelten erst für neue Sitzungen. Die Diagnose zeigt ihre eigenen rtprio-/memlock-Limits und kennzeichnet diese Herkunft. Separate Treiberprozesse können andere Grenzen besitzen.

Der CLI-Kern benötigt nur Python. Für ROS-Messwerte sind Jazzy, rclpy und die zum Treiber gehörenden Nachrichtenpakete erforderlich. Qt wird nur für die GUI gebraucht. Installation und Hardwarestart sind getrennte Vorgänge.

## Manuelle Prüfschritte

- [ ] Aktiv gebooteten Kernel und aktuelle Sitzungslimits prüfen.
- [ ] Jazzy- und Workspace-Setup vorhanden prüfen.
- [ ] Benötigte ROS-Nachrichtenpakete und python-can prüfen.

## Beleggrundlage

Fachliche Zusammenfassung lokaler MuR-Diagnosesitzungen (Juni–September 2026) und aktueller Host-/Launch-Konfigurationen in match_mobile_robotics_jazzy. Rohchats und Zugangsdaten werden nicht veröffentlicht. Nicht bestätigte Ursachen sind im Text ausdrücklich als Vermutung gekennzeichnet.
