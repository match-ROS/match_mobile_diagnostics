# Hubsäulenhöhe springt zwischen Robotern

Bestätigter Fall (September 2026): C und D publizierten gleiche Lift-Joint-Namen auf /joint_states. Die GUI mischte dadurch Höhen von zwei physischen Robotern. Der Wechsel auf /<namespace>/ewellix_lift_l/state bzw. ewellix_lift_r/state löste die falsche Zuordnung.

Auch frische JointState-Nachrichten können von einer Bridge gehaltene alte Werte enthalten. Für Hardwarefrische ausschließlich das neue Kommunikationssignal verwenden. Ein zeitgleicher „Drive #1“-Fehler war historisch beobachtet, seine Verursachung durch den falschen Jog aber nicht bewiesen.

## Manuelle Prüfschritte

- [ ] Roboter- und seitenspezifisches Ewellix-State-Topic prüfen.
- [ ] Globales /joint_states nicht als eindeutige Hubsäulenquelle verwenden.

## Beleggrundlage

Fachliche Zusammenfassung lokaler MuR-Diagnosesitzungen (Juni–September 2026) und aktueller Host-/Launch-Konfigurationen in match_mobile_robotics_jazzy. Rohchats und Zugangsdaten werden nicht veröffentlicht. Nicht bestätigte Ursachen sind im Text ausdrücklich als Vermutung gekennzeichnet.
