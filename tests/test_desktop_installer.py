"""The desktop installer is repeatable, safe for existing user files, and launchable."""
import importlib.util
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('install_desktop', ROOT / 'scripts/install_desktop.py')
DESKTOP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DESKTOP)


def test_installs_menu_icon_and_desktop_shortcut_idempotently(tmp_path, monkeypatch):
    original_which = shutil.which
    monkeypatch.setattr(DESKTOP.shutil, 'which', lambda name: None if name == 'gio' else original_which(name))
    data_home = tmp_path / 'share'
    bin_dir = tmp_path / 'bin'
    desktop_dir = tmp_path / 'Schreibtisch'
    desktop_dir.mkdir()
    first, trusted = DESKTOP.install(ROOT, data_home, bin_dir, desktop_dir=desktop_dir, check_gui=False)
    assert trusted is None
    assert (bin_dir / 'mur-diagnostics').is_symlink()
    entry = first['application'].read_text()
    assert 'Name=MuR Diagnose' in entry
    assert f'Exec="{bin_dir / "mur-diagnostics"}" gui' in entry
    assert 'Icon=match-mobile-diagnostics' in entry
    assert first['shortcut'].read_text() == entry
    assert first['shortcut'].stat().st_mode & 0o111
    for key, asset in DESKTOP.ASSETS.items():
        assert first[key].read_bytes() == (ROOT / asset).read_bytes()
    if shutil.which('desktop-file-validate'):
        subprocess.run(['desktop-file-validate', str(first['application']), str(first['shortcut'])], check=True)
    second, _ = DESKTOP.install(ROOT, data_home, bin_dir, desktop_dir=desktop_dir, check_gui=False)
    assert first == second
    assert subprocess.run([str(bin_dir / 'mur-diagnostics'), '--version'], capture_output=True,
                          text=True, check=True).stdout.strip()


def test_foreign_entry_blocks_all_writes(tmp_path):
    data_home = tmp_path / 'share'
    application = data_home / 'applications/match-mobile-diagnostics.desktop'
    application.parent.mkdir(parents=True)
    application.write_text('foreign app')
    with pytest.raises(ValueError, match='Fremde'):
        DESKTOP.install(ROOT, data_home, tmp_path / 'bin', desktop_dir=None, check_gui=False)
    assert application.read_text() == 'foreign app'
    assert not (tmp_path / 'bin').exists()


def test_existing_custom_launcher_with_spaces_is_quoted(tmp_path):
    launcher = tmp_path / 'custom bin/mur-diagnostics'
    launcher.parent.mkdir()
    launcher.write_text('#!/bin/sh\nexit 0\n')
    launcher.chmod(0o755)
    files, _ = DESKTOP.install(ROOT, tmp_path / 'share', tmp_path / 'unused bin',
                               launcher=launcher, desktop_dir=None, check_gui=False)
    assert f'Exec="{launcher}" gui' in files['application'].read_text()
    assert not (tmp_path / 'unused bin').exists()
    if shutil.which('desktop-file-validate'):
        subprocess.run(['desktop-file-validate', str(files['application'])], check=True)


def test_explicit_default_launcher_does_not_reinstall_cli(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    launcher = bin_dir / 'mur-diagnostics'
    launcher.write_text('#!/bin/sh\nexit 0\n')
    launcher.chmod(0o755)
    files, _ = DESKTOP.install(ROOT, tmp_path / 'share', bin_dir, launcher=launcher,
                               desktop_dir=None, check_gui=False)
    assert files['application'].is_file()
    assert launcher.read_text() == '#!/bin/sh\nexit 0\n'


def test_invalid_or_missing_gui_dependencies_stop_before_install(tmp_path, monkeypatch):
    monkeypatch.setattr(DESKTOP, '_gui_available', lambda: False)
    data_home = tmp_path / 'share'
    with pytest.raises(ValueError, match='PyQt5'):
        DESKTOP.install(ROOT, data_home, tmp_path / 'bin')
    assert not data_home.exists()
    with pytest.raises(ValueError, match='nicht unterstützte Zeichen'):
        DESKTOP._exec_path(Path('/tmp/mur%diagnostics'))


def test_snap_ide_data_home_does_not_hide_application_from_normal_session(tmp_path):
    home = tmp_path / 'user'
    env = {'SNAP': '/snap/code/249', 'XDG_DATA_HOME': str(home / 'snap/code/249/.local/share')}
    assert DESKTOP._default_data_home(home, env) == home / '.local/share'
    env.pop('SNAP')
    assert DESKTOP._default_data_home(home, env) == home / 'snap/code/249/.local/share'
