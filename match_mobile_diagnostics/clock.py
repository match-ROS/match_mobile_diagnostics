"""Read-only Chrony and cross-host clock observations.

Chrony monitoring commands are local to the inspected computer. A short SSH
round trip provides a conservative interval for the clock difference; it never
sets either clock.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import glob
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time

from .models import result
from . import processes

EXPECTED_MAX_SYSTEM_OFFSET_SECONDS = 0.1
EXPECTED_MAX_PAIR_OFFSET_SECONDS = 0.5
MAX_CONFIGURATION_FILES = 64
MAX_CONFIGURATION_BYTES = 128 * 1024


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_config(path: Path | None = None):
    """Read source directives from common Chrony files and their includes."""
    if path is None:
        path = next((candidate for candidate in (Path('/etc/chrony/chrony.conf'), Path('/etc/chrony.conf'))
                     if candidate.is_file()), Path('/etc/chrony/chrony.conf'))
    pending = [path]
    visited = set()
    sources = []
    problems = []
    total_bytes = 0
    while pending:
        current = pending.pop(0)
        if current in visited:
            continue
        visited.add(current)
        if len(visited) > MAX_CONFIGURATION_FILES:
            problems.append('Zu viele Chrony-Konfigurationsdateien')
            break
        try:
            data = current.read_bytes()
        except OSError as exc:
            problems.append(f'{current}: {exc.strerror or type(exc).__name__}')
            continue
        total_bytes += len(data)
        if total_bytes > MAX_CONFIGURATION_BYTES:
            problems.append('Chrony-Konfiguration überschreitet das Leselimit')
            break
        for line in data.decode('utf-8', 'replace').splitlines():
            stripped = line.strip()
            if not stripped or stripped[0] in '#!;%':
                continue
            try:
                tokens = shlex.split(stripped, comments=True)
            except ValueError:
                problems.append(f'{current}: ungültige Konfigurationszeile')
                continue
            if len(tokens) < 2:
                continue
            directive = tokens[0].lower()
            if directive in ('server', 'pool', 'peer'):
                sources.append({'directive': directive, 'name': tokens[1], 'file': str(current)})
            elif directive in ('confdir', 'sourcedir'):
                suffix = '.conf' if directive == 'confdir' else '.sources'
                for folder in tokens[1:]:
                    directory = Path(folder)
                    if not directory.is_absolute():
                        directory = current.parent / directory
                    if directory.is_dir():
                        pending.extend(sorted(p for p in directory.iterdir() if p.is_file() and p.suffix == suffix))
                    else:
                        problems.append(f'{directory}: Verzeichnis fehlt')
            elif directive == 'include':
                for pattern in tokens[1:]:
                    if not Path(pattern).is_absolute():
                        pattern = str(current.parent / pattern)
                    matches = [Path(item) for item in sorted(glob.glob(pattern))]
                    if matches:
                        pending.extend(matches)
                    else:
                        problems.append(f'{pattern}: Include nicht lesbar')
    return sources, problems


def _chronyc(arguments):
    if shutil.which('chronyc') is None:
        return None, 'chronyc nicht installiert oder nicht im PATH'
    try:
        completed = subprocess.run(['chronyc', '-n', *arguments], capture_output=True, text=True,
                                   errors='replace', timeout=3, env={**os.environ, 'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f'{type(exc).__name__}: {exc}'
    if completed.returncode:
        return None, (completed.stderr or completed.stdout).strip()[-350:] or f'chronyc Exit {completed.returncode}'
    return completed.stdout, None


def _tracking(text):
    fields = {}
    for line in text.splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            fields[key.strip().lower()] = value.strip()
    offset_match = re.search(r'([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s+seconds', fields.get('system time', ''))
    offset = float(offset_match.group(1)) if offset_match else None
    if offset is not None and not math.isfinite(offset):
        offset = None
    return {'leap_status': fields.get('leap status'), 'system_offset_seconds': offset,
            'reference': fields.get('reference id'), 'ref_time': fields.get('ref time (utc)')}


def _selected_source(text):
    for line in text.splitlines():
        match = re.match(r'^\s*([\^=#])([*+?x~\-])\s+(\S+)', line)
        if match and match.group(2) == '*':
            columns = line.split()
            sample = columns[5] if len(columns) > 5 else '?'
            age_match = re.fullmatch(r'(\d+)([smhdy]?)', sample)
            units = {'': 1, 's': 1, 'm': 60, 'h': 3600, 'd': 86400, 'y': 31536000}
            age = int(age_match.group(1)) * units[age_match.group(2)] if age_match else None
            return {'mode': match.group(1), 'name': match.group(3), 'last_good_sample_age_seconds': age}
    return None


def collect_clock(expected_server: str, role='robot', *, config_path=None):
    """Inspect config and daemon state without contacting or modifying the NTP server."""
    if role not in ('robot', 'observer'):
        raise ValueError('Unbekannte Zeitprüfrolle')
    label = 'Roboter-PC' if role == 'robot' else 'GUI-Rechner'
    prefix = f'clock.{role}'
    observed_at = _utc_now()
    sources, problems = _read_config(Path(config_path) if config_path else None)
    matching = [item for item in sources if item['directive'] == 'server' and item['name'] == expected_server]
    config_actual = {'sources': sources, 'read_issues': problems}
    if matching:
        config_status = 'pass'
    elif problems:
        config_status = 'unknown'
    else:
        config_status = 'fail'
    results = [result(f'{prefix}.config', 'Zeit', config_status,
                      f'Chrony-Serverkonfiguration auf {label}', expected=f'server {expected_server}',
                      actual=config_actual, source='Chrony-Konfigurationsdateien', observed_at=observed_at,
                      age_seconds=0.0, knowledge_id='clock_sync',
                      next_steps=[] if config_status == 'pass' else
                      [f'Chrony-Konfiguration auf {label} prüfen; erwartet wird server {expected_server}.'])]
    if shutil.which('chronyc') is None:
        results.append(result(f'{prefix}.available', 'Zeit', 'fail', f'Chrony-Abfrage auf {label} nicht installiert',
                              expected='chronyc verfügbar', actual='chronyc fehlt', source='PATH',
                              observed_at=observed_at, age_seconds=0.0, knowledge_id='clock_sync',
                              next_steps=[f'Chrony-Installation und Dienst auf {label} prüfen.']))
        tracking_text = sources_text = None
        tracking_error = sources_error = 'chronyc fehlt'
    else:
        results.append(result(f'{prefix}.available', 'Zeit', 'pass', f'Chrony-Abfrage auf {label} verfügbar',
                              expected='chronyc verfügbar', actual='chronyc vorhanden', source='PATH',
                              observed_at=observed_at, age_seconds=0.0, knowledge_id='clock_sync'))
        tracking_text, tracking_error = _chronyc(['tracking'])
        sources_text, sources_error = _chronyc(['sources'])
    selected = _selected_source(sources_text) if sources_text is not None else None
    tracking = _tracking(tracking_text) if tracking_text is not None else None
    if sources_text is None:
        source_status = 'unknown'
    elif selected is None:
        source_status = 'fail' if tracking and tracking['leap_status'] != 'Normal' else 'unknown'
    elif selected['mode'] != '^' or selected['name'] != expected_server:
        source_status = 'fail'
    elif selected['last_good_sample_age_seconds'] is None:
        source_status = 'unknown'
    else:
        source_status = 'pass' if selected['last_good_sample_age_seconds'] <= 300 else 'warn'
    results.append(result(f'{prefix}.source', 'Zeit', source_status, f'Aktive Zeitquelle auf {label}',
                          expected=expected_server, actual=selected or sources_error or 'keine Quelle ausgewählt',
                          source='chronyc -n sources', observed_at=observed_at, age_seconds=0.0,
                          knowledge_id='clock_sync', next_steps=[] if source_status == 'pass' else
                          [f'Ausgewählte Chrony-Quelle und Erreichbarkeit von {expected_server} auf {label} prüfen.']))
    if tracking is None or tracking['leap_status'] is None or tracking['system_offset_seconds'] is None:
        sync_status = 'unknown'
    elif tracking['leap_status'] != 'Normal':
        sync_status = 'fail'
    elif abs(tracking['system_offset_seconds']) > EXPECTED_MAX_SYSTEM_OFFSET_SECONDS:
        sync_status = 'warn'
    else:
        sync_status = 'pass'
    results.append(result(f'{prefix}.sync', 'Zeit', sync_status, f'Zeitsynchronisation auf {label}',
                          expected={'leap_status': 'Normal', 'max_system_offset_seconds': EXPECTED_MAX_SYSTEM_OFFSET_SECONDS},
                          actual=tracking or tracking_error, source='chronyc -n tracking',
                          observed_at=observed_at, age_seconds=0.0, knowledge_id='clock_sync',
                          next_steps=[] if sync_status == 'pass' else
                          [f'Chrony-Dienst, letzte gültige Messung und Systemzeit auf {label} prüfen.']))
    return results


def compare_remote_clock(target: str):
    """Bound remote-minus-local clock difference by an SSH round trip."""
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]*', target):
        raise ValueError('Ungültiges SSH-Ziel')
    remote_code = 'import time; print(time.time_ns())'
    argv = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '-o', 'ServerAliveInterval=5',
            '-o', 'ServerAliveCountMax=1', target, 'python3 -c ' + shlex.quote(remote_code)]
    before_wall = time.time_ns()
    before_mono = time.monotonic_ns()
    try:
        code, output, error = processes.run(argv, timeout=8)
    except (OSError, subprocess.TimeoutExpired) as exc:
        code, output, error = -1, '', f'{type(exc).__name__}: {exc}'
    after_mono = time.monotonic_ns()
    after_wall = time.time_ns()
    observed_at = _utc_now()
    elapsed = after_mono - before_mono
    clock_jump = abs((after_wall - before_wall) - elapsed) > 100_000_000
    try:
        remote_ns = int(output.strip()) if code == 0 else None
    except ValueError:
        remote_ns = None
    if remote_ns is None or clock_jump or elapsed <= 0:
        return result('clock.pair.offset', 'Zeit', 'unknown', 'Zeitdifferenz zwischen GUI-Rechner und Roboter nicht messbar',
                      expected={'max_absolute_difference_ms': EXPECTED_MAX_PAIR_OFFSET_SECONDS * 1000},
                      actual='Lokale Uhr sprang während der Messung' if clock_jump else (error or output)[-350:],
                      source=f'SSH-Zeitprobe {target}', observed_at=observed_at, age_seconds=0.0,
                      knowledge_id='clock_sync')
    # The remote sample happened between the two local readings. Any network or
    # process delay widens this interval; it cannot create a false pass/fail.
    lower_ms = (remote_ns - after_wall) / 1_000_000
    upper_ms = (remote_ns - before_wall) / 1_000_000
    maximum = max(abs(lower_ms), abs(upper_ms))
    minimum = 0.0 if lower_ms <= 0 <= upper_ms else min(abs(lower_ms), abs(upper_ms))
    limit_ms = EXPECTED_MAX_PAIR_OFFSET_SECONDS * 1000
    status = 'pass' if maximum <= limit_ms else ('fail' if minimum > limit_ms else 'unknown')
    midpoint_ms = (lower_ms + upper_ms) / 2
    actual = {'remote_minus_gui_ms': round(midpoint_ms, 3), 'uncertainty_ms': round((upper_ms - lower_ms) / 2, 3),
              'round_trip_ms': round(elapsed / 1_000_000, 3), 'interval_ms': [round(lower_ms, 3), round(upper_ms, 3)]}
    return result('clock.pair.offset', 'Zeit', status, 'Zeitdifferenz Roboter gegen GUI-Rechner',
                  expected={'max_absolute_difference_ms': limit_ms}, actual=actual,
                  source=f'SSH-Zeitprobe {target}', observed_at=observed_at, age_seconds=0.0,
                  knowledge_id='clock_sync', next_steps=[] if status == 'pass' else
                  ['Chrony-Quellen beider Rechner und Netzwerkweg zur Zeitquelle prüfen; bei hoher SSH-Latenz Messung wiederholen.'])


def clock_stat(results):
    """Compact summary for the GUI; detailed evidence remains in results."""
    checks = {item['id']: item for item in results if item.get('id', '').startswith('clock.')}
    if not checks:
        return None
    lines = []
    for role, label in (('robot', 'Roboter'), ('observer', 'GUI-PC')):
        available = checks.get(f'clock.{role}.available')
        source = checks.get(f'clock.{role}.source')
        sync = checks.get(f'clock.{role}.sync')
        if available:
            if available['status'] == 'fail':
                lines.append(f'{label}: Chrony fehlt')
            elif source and sync and source['status'] == sync['status'] == 'pass':
                lines.append(f'{label}: synchron mit {source["expected"]}')
            else:
                lines.append(f'{label}: Quelle/Sync prüfen')
    if 'clock.observer.available' not in checks:
        lines.append('GUI läuft auf dem Roboter-PC')
    pair = checks.get('clock.pair.offset')
    if pair:
        actual = pair.get('actual')
        if isinstance(actual, dict) and isinstance(actual.get('remote_minus_gui_ms'), (int, float)):
            lines.append(f'Abweichung: {actual["remote_minus_gui_ms"]:+.1f} ms ± {actual["uncertainty_ms"]:.1f} ms')
        else:
            lines.append('Abweichung: nicht messbar')
    statuses = [item['status'] for item in checks.values()]
    status = next((item for item in ('fail', 'unknown', 'warn') if item in statuses), 'pass')
    latest = max(checks.values(), key=lambda item: item.get('observed_at') or '')
    return {'text': '\n'.join(lines), 'status': status, 'source': 'Chrony-Konfiguration, chronyc, SSH-Zeitprobe',
            'observed_at': latest.get('observed_at'),
            'age_seconds': max((item.get('age_seconds') or 0.0) for item in checks.values())}
