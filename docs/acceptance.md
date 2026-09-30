# Abnahme und bekannte Grenzen

Stand: **29. September 2026, Version 0.1.0**. Die Diagnose wurde automatisiert und lesend an allen vier Roboter-PCs geprüft. Die Prüfabläufe funktionieren; die Roboter waren dabei **nicht durchgehend betriebsbereit**. Die folgenden Zustände sind zeitgebundene Beobachtungen, keine dauerhaften Eigenschaften oder Sicherheitsfreigaben.

## Abgeschlossene technische Prüfungen

| Prüfung | Ergebnis |
| --- | --- |
| Python-/Qt-Tests | **112 bestanden**, einschließlich Signalalterung, Namespace-/Domain-Zuordnung, Statusklassifikation, Controlleranzeige, GUI-Abbruch und Sitzungsisolation. Qt im Offscreen-Modus. |
| Paketierung | Wheel für 0.1.0 gebaut und CLI-Einstieg geprüft; separater Colcon-Build einschließlich ROS-Paketeinstieg erfolgreich. Lokale Artefakte: `/tmp/mur-diagnostics-dist/`, `/tmp/mur-diagnostics-colcon/`. |
| Explizite CLI-Installation | Auf dem Entwicklungsrechner und auf **mur620a–d** installiert; Versionsabfrage jeweils 0.1.0. Einstieg: `~/.local/bin/mur-diagnostics`. Die Diagnosescans installieren nichts. |
| Separater Ewellix-Patch | **6 C++-Tests mit AddressSanitizer/UndefinedBehaviorSanitizer** sowie **5 Installer-Tests** bestanden. Der Patch wurde nicht auf die laufenden Roboter-Treiber angewendet; diese wurden nicht neu gestartet. |
| GUI mit echten SSH-Diagnosen | Auswahl **D → C**, Befundanzeige und Berichtsexporte erfolgreich geprüft. Roboterspezifische Akkuwerte wechselten mit der Auswahl. |
| Persistenter Watch auf D | Drei aktuelle Stichproben im Abstand von 2 s empfangen; Beenden und anschließende Prozessbereinigung geprüft. Unmittelbar nach Abbruch waren noch auslaufende Prozesse sichtbar; eine separate spätere Prüfung fand keine verbliebenen Diagnoseprozesse. |

Wiederholbare Prüfeinstiege im Diagnose-Repository:

```bash
python3 -m pytest tests -q
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_gui.py -q
python3 -m pip wheel --no-build-isolation --no-deps . --wheel-dir /tmp/mur-diagnostics-dist
```

Colcon-Aufruf und explizite Installation stehen in der [README](../README.md); Build-/Testanweisungen für die unabhängig verwaltete Treiberänderung im [Ewellix-Patch](../../match_mobile_robotics_jazzy/patches/README.md).

## Live-Abnahme der vier Roboter

Die vollständigen SSH-Scans wurden im Modus `operational` mit Beobachtungsdomain **62** und den Namespaces **`/mur620a` bis `/mur620d`** ausgeführt. Alle vier Hostidentitäten wurden bestätigt. Die Berichte tragen `checked_at = 2026-09-29T17:07:45+00:00`; die einzelnen UR-/ROS-Beobachtungen reichen bis **17:07:58 UTC**. Jeder Scan lieferte wegen tatsächlicher Fehler den Diagnose-Exitcode **1** und `complete=false`, keinen fehlerfreien Gesamtzustand.

| Roboter | Fehler / Hinweise / nicht prüfbar | Wesentliche aktuelle Befunde |
| --- | --- | --- |
| mur620a | 8 / 0 / 9 | Kein Treiber im ausgewählten ROS-Namespace und kein passender lokaler Treiberprozess beobachtet. `can0` DOWN/STOPPED. Beide UR-Dashboards erreichbar: POWER_OFF, NORMAL, Programm ROS.urp gestoppt. |
| mur620b | 7 / 0 / 9 | Kein ausgewählter ROS-Treiber beobachtet; `can0` DOWN/STOPPED. Links RUNNING/NORMAL, rechts POWER_OFF/NORMAL; UR-Programme gestoppt. **Aktuelle Namensauflösung korrekt:** links `.89`, rechts `.90`. |
| mur620c | 7 / 1 / 7 | Laufende ausgewählte Treiber in Domain 62. MiR-Akku **31,2 %**; Aufbauakku unbekannt bei `can0` DOWN. Empfangene Lift-Höhen links etwa **0,010 m**, rechts **0,211 m**, Hardwarefrische ausdrücklich unbestätigt. Beide URs POWER_OFF/NORMAL. |
| mur620d | 7 / 1 / 10 | Laufende ausgewählte Treiber in Domain 62. MiR-Akku **45,8 %**, Aufbauakku **5,3 %**, als niedrig gemeldet. Links **ROBOT_EMERGENCY_STOP**, rechts Dashboard-Timeout. Installierte `ur_gz.ros2_control.xacro` fehlt oder ihr Symlink-Ziel fehlt. Lift-Höhen etwa **0,000 / 0,003 m**, Hardwarefrische unbestätigt. |

Die bei B früher beobachtete Namensauflösung auf `.91/.92` ist ein **historischer Befund** und wird durch den finalen Scan nicht mehr bestätigt. Daraus folgt keine Aussage, welcher andere Vorgang die Änderung vorgenommen hat. Die Sollprofile wurden durch die Diagnose nicht automatisch angepasst.

Auf C und D wurden Domain 62 und die jeweiligen Roboter-Namespaces sowohl im ROS-Graph als auch an passenden lokalen Treiberprozessen nachgewiesen. Der zusätzliche Discovery-Vergleich vom Laptop bestand für beide. Eine nichtinteraktive SSH-Sitzung ohne `ROS_DOMAIN_ID` wurde getrennt von der explizit gewählten Beobachtungsdomain und den tatsächlichen Treiberdomains behandelt.

Die Controller-Manager beider Arme waren bei allen vier Robotern nicht aktuell abfragbar; aktive ROS-Controller sind deshalb **nicht live bestätigt**. Auch die UR-Rückverbindungen waren nicht vorhanden. Eine erreichbare Dashboard-Schnittstelle oder ein geladenes UR-Programm wurde damit nicht fälschlich als vollständige Treiberbereitschaft gewertet.

Der zusätzliche **Preflight auf mur620a** vom **17:12:49 UTC** bestätigte die abweichende Bewertung vor dem Treiberstart: **0 Fehler, 1 Hinweis, 1 nicht prüfbarer Check** (32 bestanden, 13 nicht anwendbar). Beide URs in `POWER_OFF` und der noch nicht sichtbare Treiber wurden korrekt als zulässiger Ausgangszustand bewertet. `can0` DOWN blieb ein Hinweis; das fehlende Hardwarelog blieb `unknown`. Der Bericht blieb daher ausdrücklich **unvollständig** (`complete=false`) und wurde nicht als vollständig bestanden ausgegeben.

Zur Wiederholung eines entsprechenden Einzelscans:

```bash
mur-diagnostics scan --robot mur620d --via ssh --domain-id 62 \
  --namespace /mur620d --mode operational --format json \
  --output /tmp/mur-diagnostics-mur620d-acceptance.json
mur-diagnostics watch --robot mur620d --via ssh --domain-id 62 \
  --namespace /mur620d --interval 2 --format jsonl
```

## Lokale Nachweise und verbleibende Abnahme

Die vollständigen Berichte bleiben lokal; die folgenden Pfade dienen nur als Nachweisreferenzen und sind keine ausgelieferten Repository-Artefakte:

- `/tmp/mur-diagnostics-mur620{a,b,c,d}-acceptance.json`: vier vollständige SSH-Berichte; Zusammenfassung in `/tmp/mur-diagnostics-live-summary.json`.
- `/tmp/mur-diagnostics-mur620a-preflight.json`: Vorabprüfung von A mit `checked_at = 2026-09-29T17:12:49+00:00`; UR-/ROS-Befunde bis 17:12:53 UTC.
- `/tmp/mur-diagnostics-installation.json`: erfolgreiche Installations- und Versionsausgaben aller vier Roboter-PCs; die konkreten Release-Verzeichnisnamen stehen dort.
- `/tmp/mur-diagnostics-gui-acceptance.json`: echte GUI-Auswahl D → C mit verschiedenen Akkuwerten. Dieses JSON enthält keinen eigenen Messzeitstempel.
- `/tmp/mur-diagnostics-watch-acceptance.json`: **17:08:41, 17:08:43 und 17:08:45 UTC**. Aufbauakku jeweils 5,3 %; Empfangsalter etwa 0,80–0,96 s, Lift-State-Empfangsalter etwa 0,43–0,59 s. Empfangsfrische belegt hier noch keine erfolgreiche neue Lift-Hardwareabfrage.

Offen bleiben die **physische Links-/Rechts-Bestätigung der Liftadapter auf C** und die **Live-Abnahme des separat gebauten Ewellix-Patches** nach einer ausdrücklich koordinierten Anwendung und einem späteren Treiberneustart. Die aktuelle Zuordnung auf C bleibt vorläufig. Ohne den Diagnose-Heartbeat des Patches bleiben Hardwarekommunikation und aktuelle Aktuatorzustände trotz eintreffender Lift-State-Nachrichten unbestätigt; Fehlerhistorien werden nicht als aktuelle Aktuatorfehler ausgegeben.

Die positive Anzeige aktiver UR-ROS-Controller und einer vollständigen UR-Rückverbindung ist mit automatisierten Szenarien geprüft, aber beim beobachteten Hardwarezustand nicht vollständig live validiert. Es wurden keine Fehler durch Kabelziehen, Netzwerkänderungen, Not-Halt-Betätigung oder Controllerwechsel herbeigeführt. Rohlogs, private Chatverläufe und Zugangsdaten wurden nicht in dieses Repository übernommen.

## Nachtrag vom 30. September 2026: GUI-Start aus der Offline-Installation

Version **0.1.1** behebt einen Startfehler der installierten GUI: Der CLI-Einstieg konnte das Paket durch einen nur im Elternprozess gesetzten Python-Suchpfad laden. Sein GUI-Kindprozess startete anschließend mit `python -m match_mobile_diagnostics.cli` ohne diesen Pfad und lieferte außerhalb des Repositorys `ModuleNotFoundError` statt eines Berichts. Die GUI übergibt nun den Paketpfad an ihren Kindprozess. Auf einem MuR-PC wählt sie zudem dessen Roboteridentität und lokalen Zugriff vor.

**114 Tests** und der Wheel-Bau für 0.1.1 bestanden. Der Fehler wurde aus dem installierten Release außerhalb des Repositorys zuerst reproduziert und anschließend mit einer echten **mur620a-SSH-Vorabdiagnose** geprüft. Direkt auf mur620a startete die installierte GUI im Offscreen-Test mit `mur620a / local` und zeigte einen vollständigen Teilbericht mit 15 Befundgruppen. Dieser Bericht enthielt weiterhin einen CAN-Hinweis und ein nicht prüfbares Hardwarelog; er wurde korrekt als unvollständig bewertet.

Die Installation **0.1.1** wurde auf dem Entwicklungsrechner und auf **mur620a** bestätigt. Die Aktualisierung von mur620b–d konnte an diesem Tag nicht erfolgen: SSH meldete für alle drei Ziele `No route to host`. Für diese PCs ist die installierte Version 0.1.1 damit noch nicht bestätigt.

## Nachtrag vom 30. September 2026: Zeitdiagnose und Chrony

Version **0.1.2** enthält einen versionierten Sollwert `time_server=10.145.8.50` (Profilversion 2, Herkunft: Nutzerangabe). Die GUI zeigt Chrony-Konfiguration, ausgewählte Quelle, Synchronisationszustand und bei SSH eine konservativ begrenzte Differenz zum GUI-Rechner. Ein eigener Wissensartikel beschreibt die Checks. **123 Tests** und der Wheel-Bau bestanden; Version 0.1.2 wurde auf dem Entwicklungsrechner und auf **mur620a–d** installiert.

Der Server `10.145.8.50` antwortete auf eine NTP-Anfrage (Stratum 3, kein Leap-Alarm). Auf dem Entwicklungsrechner, mur620b und mur620d wurde Chrony im separat autorisierten Administrationsschritt installiert. Die Ubuntu-Standardpools wurden gesichert und deaktiviert; `server 10.145.8.50 iburst prefer` ist aktiv. Alle drei meldeten danach `^* 10.145.8.50` und `Leap status: Normal`. Die Originaldateien liegen jeweils als `/etc/chrony/chrony.conf.match-mur-backup-*` auf den betroffenen Rechnern. Die Paketinstallation ersetzte `systemd-timesyncd`.

Die vier vollständigen, rein lesenden SSH-Vorabscans der Zeitdiagnose liegen lokal unter `/tmp/mur-clock-mur620{a,b,c,d}.json` mit `checked_at` zwischen **15:29:14 und 15:29:18 UTC**. B und D meldeten sowohl auf dem Roboter als auch auf dem GUI-Rechner Konfiguration, aktive Quelle und Synchronisation als bestanden. A und C meldeten `chronyc` und Chrony-Konfiguration auf dem Roboter als fehlend; Quell- und Laufzeitsynchronisation waren daher **nicht prüfbar**. Die direkten SSH-Zeitproben lagen für A–C vollständig innerhalb der ±500-ms-Grenze. Bei D war das Netzlaufzeitintervall mit etwa 528 ms zu breit für eine eindeutige Paarbewertung; das wird als `unknown` ausgewiesen und widerspricht nicht den bestandenen Chrony-Abfragen. Die installierte GUI zeigte auf B beide Rechner als mit `10.145.8.50` synchron und die begrenzte Zeitdifferenz.

Für A und C wurde `scripts/configure_chrony.py` unter `/tmp/mur-configure-chrony.py` bereitgestellt. Die Anwendung per SSH ist noch offen: `sudo -n` verlangt weiterhin ein Passwort. `sudo -l` zeigt jeweils eine NOPASSWD-Regel, gefolgt von einer allgemeineren Regel mit Passwortanforderung. Ohne wirksame administrative Ausführungsrechte wurden auf A und C weder Chrony installiert noch Zeitdienste umgestellt.

Der installierte SSH-`watch` auf B lieferte drei Zeitberichte im Abstand von rund zwei Sekunden (15:33:54/56/58 UTC). Im ersten Bericht lief die unabhängige GUI-Rechner-Prüfung noch (`unknown`); ab dem zweiten waren Roboterquelle, GUI-Rechnerquelle und Zeitintervall `pass`. Das Beenden lieferte Exitcode 130 und ließ keinen laufenden lokalen Beobachtungsprozess zurück.

Die Zeitkarte wurde für die verfügbare GUI-Höhe auf zwei Zeilen verdichtet; alle Messdetails bleiben in den Befunden. Diese Darstellung wurde mit einem echten B-Bericht visuell geprüft. Die finale Version **0.1.3** wurde mit **123 bestandenen Tests** und gebautem Wheel auf dem Entwicklungsrechner sowie auf allen vier MuR-PCs installiert. Die ausstehenden Chrony-Installationen auf A und C sind davon unabhängig.
