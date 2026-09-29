# UR-Dashboard, Remote-Modus und Sicherheitszustand

Die lesenden Dashboard-Abfragen laufen auf TCP-Port 29999. Ein Timeout erlaubt keine Aussage über Remote-Modus oder Not-Halt. Eine bestätigte Antwort wie ROBOT_EMERGENCY_STOP oder PROTECTIVE_STOP ist dagegen ein konkreter Sicherheitsbefund.

Softwaregenerationen unterstützen unterschiedliche Abfragen. Insbesondere eine nicht unterstützte Remote-Control-Abfrage ist kein Beleg für lokalen Betrieb. safetymode kann als Fallback dienen, wenn safetystatus nicht verfügbar ist.

POWER_OFF bei normalem Sicherheitszustand war im September 2026 fälschlich als blockierender Vorabfehler behandelt worden. Im Modus vor dem Hardwarestart ist ausgeschaltet zulässig; bei erwarteter Betriebsbereitschaft wird die Abweichung angezeigt. Sicherheitsstopps nach Betriebsanweisung und Prüfung der tatsächlichen Ursache vor Ort behandeln. Der Helper sendet keine Freigabe-, Entriegelungs- oder Startbefehle.

Referenz: https://www.universal-robots.com/articles/ur/dashboard-server-cb-series-port-29999/

## Manuelle Prüfschritte

- [ ] Am richtigen Teach Pendant den aktuellen Sicherheitszustand prüfen.
- [ ] Bei unterstützten Robotermodellen den Remote-Modus prüfen.
- [ ] Ausgewähltes PolyScope-Programm und External-Control-Knoten prüfen.

## Beleggrundlage

Fachliche Zusammenfassung lokaler MuR-Diagnosesitzungen (Juni–September 2026) und aktueller Host-/Launch-Konfigurationen in match_mobile_robotics_jazzy. Rohchats und Zugangsdaten werden nicht veröffentlicht. Nicht bestätigte Ursachen sind im Text ausdrücklich als Vermutung gekennzeichnet.
