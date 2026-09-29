# Programm läuft, aber UR-Treiber ist nicht verbunden

Ein laufendes beliebiges PolyScope-Programm belegt keine funktionierende External-Control-Verbindung. Die GUI zeigt deshalb das geladene PolyScope-Programm und aktive ROS-Controller getrennt.

Der PC muss unter 192.168.12.69 erreichbar sein. Die aktuellen Launch-Defaults verwenden links Ports 50005–50008, rechts 50001–50004; Reverse selbst liegt links auf 50005 und rechts auf 50001. Der Helper liest Socketzustände, sendet aber keine Daten an diese Steuerschnittstellen.

Bestätigter Fall (Juli 2026): Reverse war kurz verbunden und fiel sofort wieder ab. Die damalige GUI hatte einen früheren OK-Zustand gespeichert. Deshalb aktuelle Verbindung und Treiber-Lebendigkeit kombinieren und Verbindungsverlust sofort sichtbar machen. Erfolgreiche Logzeilen wie „Robot connected to reverse interface“ sind keine Warnungen.

## Manuelle Prüfschritte

- [ ] Im External-Control-Knoten die PC-Adresse und den Port ablesen.
- [ ] Aktive ROS-Controller und Treiberstatus kontrollieren.
- [ ] Aktuelle Reverse-Verbindung und aktuelle Abbruchmeldungen vergleichen.

## Beleggrundlage

Fachliche Zusammenfassung lokaler MuR-Diagnosesitzungen (Juni–September 2026) und aktueller Host-/Launch-Konfigurationen in match_mobile_robotics_jazzy. Rohchats und Zugangsdaten werden nicht veröffentlicht. Nicht bestätigte Ursachen sind im Text ausdrücklich als Vermutung gekennzeichnet.
