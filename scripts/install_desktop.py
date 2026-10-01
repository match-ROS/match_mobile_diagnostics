#!/usr/bin/env python3
"""Install the MuR Diagnose application entry and icon for the current user."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

NAME = 'match-mobile-diagnostics'
MARKER = 'match_mobile_diagnostics/scripts/install_desktop.py'
ASSETS = {
    'svg': Path('assets/mur-diagnostics.svg'),
    '48': Path('assets/mur-diagnostics-48.png'),
    '128': Path('assets/mur-diagnostics-128.png'),
}


def _cli_installer(source):
    spec = importlib.util.spec_from_file_location('mur_install_cli', source / 'scripts/install_cli.py')
    if spec is None or spec.loader is None:
        raise ValueError('CLI-Installer fehlt im Repository')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gui_available():
    try:
        response = subprocess.run(['/usr/bin/python3', '-c', 'from PyQt5 import QtWidgets'],
                                  capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return response.returncode == 0


def _default_data_home(home, environ):
    value = environ.get('XDG_DATA_HOME')
    if not value:
        return home / '.local/share'
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return home / '.local/share'
    # IDEs started as Snaps export a private XDG directory. The regular
    # Ubuntu desktop session does not search that directory for applications.
    if environ.get('SNAP') and candidate.is_relative_to(home / 'snap'):
        return home / '.local/share'
    return candidate


def _desktop_dir(home):
    if shutil.which('xdg-user-dir'):
        try:
            response = subprocess.run(['xdg-user-dir', 'DESKTOP'], capture_output=True, text=True, timeout=3)
            candidate = Path(response.stdout.strip())
            if response.returncode == 0 and candidate.is_absolute() and candidate.is_dir():
                return candidate
        except (OSError, subprocess.TimeoutExpired):
            pass
    for name in ('Desktop', 'Schreibtisch'):
        candidate = home / name
        if candidate.is_dir():
            return candidate
    return None


def _exec_path(path):
    value = str(path)
    # Exec is not a shell command. Restrict exotic field-code/escape characters
    # instead of guessing how a desktop implementation will interpret them.
    if not re.fullmatch(r'[A-Za-z0-9_./+@\- ]+', value) or '=' in value:
        raise ValueError(f'CLI-Pfad enthält für Desktop-Dateien nicht unterstützte Zeichen: {path}')
    return '"' + value + '"'


def _desktop_entry(launcher):
    return ('[Desktop Entry]\n'
            'Type=Application\n'
            'Name=MuR Diagnose\n'
            'GenericName=Roboterdiagnose\n'
            'Comment=Hardware, Netzwerk und Treiber von MuR-Robotern prüfen\n'
            f'Exec={_exec_path(launcher)} gui\n'
            f'Icon={NAME}\n'
            'Terminal=false\n'
            'Categories=Utility;\n'
            'Keywords=MuR;Roboter;Diagnose;ROS;Hardware;\n'
            'StartupNotify=true\n'
            f'X-Match-Mobile-Diagnostics-Managed={MARKER}\n')


def _atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('wb', dir=path.parent, prefix='.' + NAME + '-', delete=False) as temp:
        temp.write(data)
        temporary = Path(temp.name)
    try:
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _managed_paths(data_home, desktop_dir):
    paths = {
        'application': data_home / 'applications' / (NAME + '.desktop'),
        'svg': data_home / 'icons/hicolor/scalable/apps' / (NAME + '.svg'),
        '48': data_home / 'icons/hicolor/48x48/apps' / (NAME + '.png'),
        '128': data_home / 'icons/hicolor/128x128/apps' / (NAME + '.png'),
    }
    if desktop_dir is not None:
        paths['shortcut'] = desktop_dir / (NAME + '.desktop')
    return paths


def install(source, data_home, bin_dir, *, desktop_dir=None, launcher=None, check_gui=True):
    source = Path(source).expanduser().resolve()
    data_home = Path(data_home).expanduser().absolute()
    bin_dir = Path(bin_dir).expanduser().absolute()
    if desktop_dir is not None:
        desktop_dir = Path(desktop_dir).expanduser().absolute()
    use_existing_launcher = launcher is not None
    launcher = Path(launcher).expanduser().absolute() if use_existing_launcher else bin_dir / 'mur-diagnostics'
    if check_gui and launcher == bin_dir / 'mur-diagnostics' and not _gui_available():
        raise ValueError('PyQt5 fehlt im System-Python. Zuerst python3-pyqt5 installieren.')
    if use_existing_launcher and not (launcher.is_file() and os.access(launcher, os.X_OK)):
        raise ValueError(f'CLI-Einstieg fehlt oder ist nicht ausführbar: {launcher}')
    entry = _desktop_entry(launcher).encode('utf-8')
    paths = _managed_paths(data_home, desktop_dir)
    source_assets = {}
    for key, relative in ASSETS.items():
        asset = source / relative
        if asset.is_symlink() or not asset.is_file():
            raise ValueError(f'Icon-Datei fehlt oder ist ein Symlink: {asset}')
        source_assets[key] = asset.read_bytes()
    planned = {'application': entry, **source_assets}
    if 'shortcut' in paths:
        planned['shortcut'] = entry
    manifest_path = data_home / 'mur-diagnostics/desktop-install.json'
    managed = set()
    if manifest_path.exists() or manifest_path.is_symlink():
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError(f'Installationsmanifest ist keine reguläre Datei: {manifest_path}')
        try:
            old = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError(f'Installationsmanifest ist ungültig: {manifest_path}') from exc
        if old.get('managed_by') != MARKER or not isinstance(old.get('paths'), list):
            raise ValueError(f'Fremdes Installationsmanifest nicht überschreiben: {manifest_path}')
        managed = set(old['paths'])
    for key, path in paths.items():
        if path.is_symlink() or path.exists() and not path.is_file():
            raise ValueError(f'Vorhandene Verknüpfung/Datei nicht überschreiben: {path}')
        if path.exists() and str(path) not in managed and path.read_bytes() != planned[key]:
            raise ValueError(f'Fremde Desktop- oder Icon-Datei nicht überschreiben: {path}')
    if not use_existing_launcher:
        installer = _cli_installer(source)
        installer.install(source, data_home / 'mur-diagnostics', bin_dir)
    for key, path in paths.items():
        desired_mode = 0o755 if key == 'shortcut' else 0o644
        if not path.exists() or path.read_bytes() != planned[key] or (path.stat().st_mode & 0o777) != desired_mode:
            _atomic_write(path, planned[key], desired_mode)
    manifest = {'managed_by': MARKER, 'launcher': str(launcher),
                'paths': sorted(set(managed) | {str(path) for path in paths.values()})}
    _atomic_write(manifest_path, (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode(), 0o644)
    trusted = None
    if 'shortcut' in paths and shutil.which('gio'):
        try:
            gio_env = os.environ.copy()
            gio_env['XDG_DATA_HOME'] = str(data_home)
            if gio_env.get('SNAP'):
                gio_env.pop('GIO_MODULE_DIR', None)
            response = subprocess.run(['gio', 'set', '--type', 'string', str(paths['shortcut']),
                                       'metadata::trusted', 'true'], capture_output=True, text=True,
                                      timeout=5, env=gio_env)
            trusted = response.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            trusted = False
    return paths, trusted


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source_default = Path(__file__).resolve().parents[1]
    home = Path.home()
    data_default = _default_data_home(home, os.environ)
    parser.add_argument('--source', type=Path, default=source_default, help='Diagnose-Repository mit assets und scripts')
    parser.add_argument('--data-home', type=Path, default=data_default, help='Benutzerdatenverzeichnis (XDG_DATA_HOME)')
    parser.add_argument('--bin-dir', type=Path, default=home / '.local/bin', help='Verzeichnis des CLI-Einstiegs')
    parser.add_argument('--launcher', type=Path, help='Vorhandenen ausführbaren CLI-Einstieg verwenden statt ihn zu installieren')
    parser.add_argument('--desktop-dir', type=Path, help='Verzeichnis für ein Desktop-Icon; Standard: XDG DESKTOP')
    parser.add_argument('--no-desktop-shortcut', action='store_true', help='Nur Eintrag für die Anwendungssuche anlegen')
    args = parser.parse_args(argv)
    desktop_dir = None if args.no_desktop_shortcut else args.desktop_dir or _desktop_dir(home)
    try:
        paths, trusted = install(args.source, args.data_home, args.bin_dir, desktop_dir=desktop_dir,
                                 launcher=args.launcher)
    except (OSError, ValueError, SyntaxError) as exc:
        print(f'Desktop-Installation abgebrochen: {exc}', file=sys.stderr)
        return 1
    print(f'Anwendungssuche: MuR Diagnose ({paths["application"]})')
    if 'shortcut' in paths:
        print(f'Desktop-Icon: {paths["shortcut"]}')
        if trusted is False:
            print('Desktop-Verknüpfung: Im Dateimanager gegebenenfalls „Starten erlauben“ wählen.', file=sys.stderr)
    else:
        print('Kein Desktop-Ordner gefunden; Anwendungssuche ist eingerichtet.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
