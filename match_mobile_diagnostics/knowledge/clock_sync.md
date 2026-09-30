# Zeitstempel und Chrony prüfen

**Sollwert (Nutzerangabe vom 30.09.2026, noch nicht als gesunder Livezustand bestätigt):** Auf jedem MuR-PC und dem Rechner mit der GUI ist `10.145.8.50` als Chrony-`server` konfiguriert. Chrony soll diese Quelle aktuell auswählen und die Systemuhr synchronisiert halten.

Die Diagnose liest Konfigurationsdateien und führt ausschließlich `chronyc tracking` und `chronyc sources` als Überwachungsbefehle aus. Bei SSH-Zugriff misst sie außerdem eine kurze Zeitprobe zwischen GUI-Rechner und Roboter. Wegen Netzlaufzeit wird die Differenz als Intervall bewertet; eine unsichere Messung bleibt „nicht prüfbar“. Ein momentaner kleiner Unterschied beweist nicht, dass die Uhren dauerhaft synchron bleiben.

Prüffolge:

1. Auf beiden Rechnern prüfen, ob Chrony und `chronyc` vorhanden sind. Fehlt `chronyc`, bleiben Laufzeitquelle und Synchronisationszustand unbekannt.
2. In `/etc/chrony/chrony.conf` und eingebundenen Dateien die aktive Zeile `server 10.145.8.50` suchen. Zusätzliche Quellen können eine andere Auswahl verursachen.
3. `chronyc -n sources` lesen: Die mit `^*` markierte Quelle soll `10.145.8.50` sein. `chronyc -n tracking` soll `Leap status: Normal` und eine Systemabweichung von höchstens 100 ms melden.
4. Bei einer SSH-Diagnose die gemessene Differenz Roboter gegen GUI-Rechner prüfen. Das vollständige Unsicherheitsintervall muss innerhalb ±500 ms liegen. Bei hoher SSH-Latenz die Messung wiederholen.
5. Falls die Quelle fehlt oder nicht ausgewählt ist, Dienstzustand, Netzwerkweg zu `10.145.8.50`, Konfiguration und eventuell dynamische Chrony-Quellen prüfen. Änderungen am System werden nicht durch das Diagnosetool ausgeführt.

Die Bedeutung von `tracking`, `sources`, `^*` sowie eingebundenen Konfigurationsdateien ist in der offiziellen [chronyc-Dokumentation](https://chrony-project.org/doc/4.6.1/chronyc.html) und [chrony.conf-Dokumentation](https://chrony-project.org/doc/4.6.1/chrony.conf.html) beschrieben.
