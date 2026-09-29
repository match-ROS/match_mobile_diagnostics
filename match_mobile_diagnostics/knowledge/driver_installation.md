# Treiberinstallation, Kalibrierung und aktuelle Logs

Bestätigte frühere Installationsfälle: doppelte Pakete in einem Backup-Verzeichnis, leere UR-Submodule nach einem Sync und gebrochene Installationssymlinks. Ein fehlendes ur_gz.ros2_control.xacro konnte den Hardwarelaunch abbrechen. Kalibrierungswarnungen müssen mit der richtigen Arm-Seriennummer und Kalibrierungsdatei abgeglichen werden.

Frühere Logs enthalten außerdem Play-/Brake-Release-Timeouts, einen Python-Fehler „bool object is not callable“ und doppelte BMS-/Bridge-Nodes. Das sind Diagnosehinweise, keine pauschalen Beweise für einen mechanischen Defekt. Fehlende Karten/TF/Odom können aus Bridge-Allowlist oder Frame-Konfiguration stammen.

Im September wurde einmal ein Protective-Stop aus einem Juli-Log fälschlich als aktuell gelesen. Der Helper verwendet nur eindeutig datierte aktuelle Meldungen als aktuelle Hinweise. Undatierte oder alte Einträge werden als Historie eingeordnet. Ein generisches ERROR-Wort genügt nicht; beispielsweise ist eine unkonfigurierte MoveIt-Octomap nicht automatisch ein Hardwaredefekt.

## Manuelle Prüfschritte

- [ ] Installierte Launch-/Xacro-Dateien und Symlink-Ziele prüfen.
- [ ] Treiberrevision und passende UR-Kalibrierungsdatei prüfen.
- [ ] Logzeitpunkt, Roboter und aktuellen Prozessstart zuordnen.

## Beleggrundlage

Fachliche Zusammenfassung lokaler MuR-Diagnosesitzungen (Juni–September 2026) und aktueller Host-/Launch-Konfigurationen in match_mobile_robotics_jazzy. Rohchats und Zugangsdaten werden nicht veröffentlicht. Nicht bestätigte Ursachen sind im Text ausdrücklich als Vermutung gekennzeichnet.
