#!/usr/bin/env python3
"""Explicit administrator step: point Ubuntu Chrony at the MuR time server.

This script is never called by scan, watch, or the GUI. Run with --apply and
root privileges after reviewing the target host. It preserves a backup.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

SERVER = '10.145.8.50'
CONFIG = Path('/etc/chrony/chrony.conf')
DROPIN = Path('/etc/chrony/conf.d/90-match-mur-time.conf')
MARKER = '# Managed by match_mobile_diagnostics/scripts/configure_chrony.py'
DROPIN_TEXT = f'{MARKER}\nserver {SERVER} iburst prefer\n'
UBUNTU_POOL = re.compile(r'^\s*pool\s+(?:ntp\.ubuntu\.com|[012]\.ubuntu\.pool\.ntp\.org)\b')
SOURCE = re.compile(r'^\s*(server|pool|peer)\s+(\S+)', re.IGNORECASE)


def updated_config(text):
    """Disable only known Ubuntu default pools; reject unexpected active sources."""
    lines = []
    unexpected = []
    for line in text.splitlines(keepends=True):
        if UBUNTU_POOL.match(line):
            lines.append('# MuR-Zeitquelle: Ubuntu-Standardpool deaktiviert: ' + line)
            continue
        match = SOURCE.match(line)
        if match and not (match.group(1).lower() == 'server' and match.group(2) == SERVER):
            unexpected.append(line.strip())
        lines.append(line)
    if unexpected:
        raise ValueError('Weitere aktive Chrony-Zeitquellen manuell prüfen: ' + ', '.join(unexpected))
    if not re.search(r'^\s*confdir\s+/etc/chrony/conf\.d\s*$', ''.join(lines), re.MULTILINE):
        raise ValueError('Chrony lädt /etc/chrony/conf.d nicht; Konfiguration manuell prüfen')
    return ''.join(lines)


def run(argv, *, timeout=90):
    return subprocess.run(argv, capture_output=True, text=True, errors='replace', timeout=timeout)


def replace_file(path, data):
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, prefix='.mur-chrony-', delete=False) as temp:
        temp.write(data)
        temp_path = Path(temp.name)
    try:
        temp_path.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def apply():
    if os.geteuid() != 0:
        raise PermissionError('Für --apply wird sudo/root benötigt')
    if shutil.which('chronyc') is None:
        proc = run(['apt-get', 'install', '-y', 'chrony'], timeout=180)
        if proc.returncode:
            raise RuntimeError('Chrony-Installation fehlgeschlagen: ' + (proc.stderr or proc.stdout)[-600:])
    if not CONFIG.is_file():
        raise FileNotFoundError(CONFIG)
    original = CONFIG.read_text(encoding='utf-8')
    planned = updated_config(original)
    if DROPIN.exists() and DROPIN.read_text(encoding='utf-8') != DROPIN_TEXT:
        raise ValueError(f'Vorhandene fremde Chrony-Datei nicht überschreiben: {DROPIN}')
    change_main = planned != original
    change_dropin = not DROPIN.exists()
    backup = None
    if change_main:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup = CONFIG.with_name(f'{CONFIG.name}.match-mur-backup-{stamp}')
        shutil.copy2(CONFIG, backup)
    try:
        if change_main:
            replace_file(CONFIG, planned)
        if change_dropin:
            DROPIN.parent.mkdir(parents=True, exist_ok=True)
            replace_file(DROPIN, DROPIN_TEXT)
        parsed = run(['chronyd', '-p', '-f', str(CONFIG)], timeout=10)
        if parsed.returncode:
            raise RuntimeError('Chrony-Konfiguration ungültig: ' + (parsed.stderr or parsed.stdout)[-500:])
        if change_main or change_dropin:
            service = run(['systemctl', 'enable', '--now', 'chrony'], timeout=15)
            if service.returncode:
                raise RuntimeError('Chrony-Dienst konnte nicht aktiviert werden: ' + service.stderr[-500:])
            service = run(['systemctl', 'restart', 'chrony'], timeout=15)
            if service.returncode:
                raise RuntimeError('Chrony-Dienst konnte nicht neu geladen werden: ' + service.stderr[-500:])
    except Exception:
        if change_main and backup is not None:
            shutil.copy2(backup, CONFIG)
        if change_dropin:
            DROPIN.unlink(missing_ok=True)
        run(['systemctl', 'restart', 'chrony'], timeout=15)
        raise
    deadline = time.monotonic() + 35
    selected = False
    normal = False
    while time.monotonic() < deadline:
        sources = run(['chronyc', '-n', 'sources'], timeout=5)
        tracking = run(['chronyc', '-n', 'tracking'], timeout=5)
        selected = sources.returncode == 0 and bool(re.search(r'^\s*\^\*\s+' + re.escape(SERVER) + r'\s', sources.stdout, re.MULTILINE))
        normal = tracking.returncode == 0 and bool(re.search(r'^Leap status\s*:\s*Normal\s*$', tracking.stdout, re.MULTILINE))
        if selected and normal:
            break
        time.sleep(2)
    print(f'Chrony-Server: {SERVER}; ausgewählt: {selected}; synchron: {normal}')
    if backup:
        print(f'Originalkonfiguration gesichert: {backup}')
    if not (selected and normal):
        print('Konfiguration installiert; Synchronisation noch nicht bestätigt. chronyc -n sources / tracking prüfen.', file=sys.stderr)
        return 2
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Chrony installieren und Konfiguration ausdrücklich anwenden')
    args = parser.parse_args(argv)
    if not args.apply:
        print(f'Geplant: Chrony installieren, Ubuntu-Standardpools deaktivieren, server {SERVER} setzen. Mit sudo und --apply ausführen.')
        return 0
    try:
        return apply()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f'Chrony-Einrichtung fehlgeschlagen: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
