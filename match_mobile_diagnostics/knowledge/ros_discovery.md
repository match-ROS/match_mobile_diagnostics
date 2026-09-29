# Daten lokal vorhanden, auf dem Laptop unsichtbar

Soll-Domain ist 62. Ein nicht gesetztes ROS_DOMAIN_ID verwendet normalerweise Domain 0. Das Tool dokumentiert die tatsächliche Sitzungsumgebung. Die sichtbar gewählte Beobachtungsdomain (--domain-id, Standard 62) gilt nur für den Diagnoseprozess; laufende Treiberdomains werden separat geprüft. Eine SSH-Sitzung auf Domain 0 ist deshalb bei ausdrücklich gewählter Beobachtungsdomain 62 allein kein Roboterfehler.

Ein mur620d kann unter /mur620 laufen. Bei abweichendem Namespace ausdrücklich auswählen, statt Daten eines anderen Roboters zuzuordnen. Namen und Zeitstempel sind Bestandteil der Belege.

Bestätigter Fall (September 2026): Auf C waren Kameradaten lokal vorhanden; die Workstation sah sie nach einer expliziten Peer-Konfiguration. ROS_STATIC_PEERS=mur620c war dort erfolgreich. Das ist ein konkreter damaliger Lösungsweg, kein universeller Nachweis, dass jede fehlende Topic-Verbindung dieselbe Ursache hat. Erst lokale Treibersicht und Laptop-Sicht vergleichen.

## Manuelle Prüfschritte

- [ ] ROS_DOMAIN_ID auf Roboter und Laptop vergleichen.
- [ ] Physische Roboteridentität und ROS-Namespace getrennt prüfen.
- [ ] Bei lokal vorhandenen Topics RMW-/Discovery-/Peer-Konfiguration vergleichen.

## Beleggrundlage

Fachliche Zusammenfassung lokaler MuR-Diagnosesitzungen (Juni–September 2026) und aktueller Host-/Launch-Konfigurationen in match_mobile_robotics_jazzy. Rohchats und Zugangsdaten werden nicht veröffentlicht. Nicht bestätigte Ursachen sind im Text ausdrücklich als Vermutung gekennzeichnet.
