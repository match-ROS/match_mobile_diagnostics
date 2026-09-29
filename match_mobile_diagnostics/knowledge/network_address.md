# UR oder MiR im internen Netzwerk nicht erreichbar

Soll: PC 192.168.12.69/24, MiR 192.168.12.20, UR10_l 192.168.12.89, UR10_r 192.168.12.90. Die vier Roboter besitzen voneinander getrennte interne Netze mit diesen gleichen Adressen. Deshalb interne Prüfungen auf dem ausgewählten Roboter-PC ausführen.

Ein vorhandener Ethernet-Link belegt noch keine korrekte IP-Adresse. Die Route zu jedem Arm muss die interne Schnittstelle und die Quelladresse .69 verwenden. Andernfalls kann der UR den Treiber nicht wie konfiguriert zurück erreichen.

Bestätigter früherer Fall (Juni 2026): .69 war auf einem zweiten direkt angeschlossenen PC vergeben; NetworkManager konnte die Verbindung nicht aktivieren. Doppelte Verbindungsprofile erschwerten die Diagnose. Wenn beide URs und der MiR trotz richtiger IP/Route gleichzeitig fehlen, ist ein gemeinsamer Switch-/Uplink-/Versorgungspfad eine mögliche Ursache. Der entsprechende Fall vom September 2026 blieb ohne bestätigte physische Reparatur; das Tool darf daraus keinen bewiesenen Kabeldefekt machen.

Lesende Befehle: `ip -br address`, `ip route get 192.168.12.89`, `ip neigh show`, `getent hosts UR10_l`.

## Manuelle Prüfschritte

- [ ] Interne PC-Adresse ist 192.168.12.69/24.
- [ ] Link-LEDs und Stromversorgung des internen Switches prüfen.
- [ ] Kabelweg vom PC zum Switch und betroffenen Gerät verfolgen.
- [ ] Am Pendant die tatsächliche UR-Adresse ablesen.

## Beleggrundlage

Fachliche Zusammenfassung lokaler MuR-Diagnosesitzungen (Juni–September 2026) und aktueller Host-/Launch-Konfigurationen in match_mobile_robotics_jazzy. Rohchats und Zugangsdaten werden nicht veröffentlicht. Nicht bestätigte Ursachen sind im Text ausdrücklich als Vermutung gekennzeichnet.
