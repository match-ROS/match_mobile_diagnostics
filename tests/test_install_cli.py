"""Exercise offline installation in isolated prefixes, including real CLI use."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_cli", REPOSITORY / "scripts/install_cli.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_install_runs_outside_repository_and_reinstallation_is_idempotent(tmp_path):
    prefix, bin_dir = tmp_path / "install with spaces", tmp_path / "bin"
    entrypoint, release = installer.install(REPOSITORY, prefix, bin_dir)
    version = subprocess.run([str(entrypoint), "--version"], cwd=tmp_path, capture_output=True, text=True, check=True)
    assert version.stdout.strip() == installer._version(installer._source_files(REPOSITORY))
    knowledge = subprocess.run([str(entrypoint), "knowledge", "list", "--format", "json"],
                               cwd=tmp_path, capture_output=True, text=True, check=True)
    assert any(item["id"] == "network_address" for item in json.loads(knowledge.stdout))
    assert (release / "LICENSE").read_bytes() == (REPOSITORY / "LICENSE").read_bytes()
    child_script = """
import os, runpy, subprocess, sys
entrypoint, release = sys.argv[1:]
os.environ.pop('PYTHONDONTWRITEBYTECODE', None)
sys.argv = [entrypoint, '--version']
try:
    runpy.run_path(entrypoint, run_name='__main__')
except SystemExit as exc:
    assert exc.code == 0
os.environ['PYTHONPATH'] = release
subprocess.run([sys.executable, '-c', 'import match_mobile_diagnostics.cli'], check=True)
"""
    subprocess.run([sys.executable, "-c", child_script, str(entrypoint), str(release)],
                   cwd=tmp_path, capture_output=True, text=True, check=True)
    before = {str(path.relative_to(release)): path.stat().st_mtime_ns for path in release.rglob("*") if path.is_file()}
    assert installer.install(REPOSITORY, prefix, bin_dir) == (entrypoint, release)
    assert before == {str(path.relative_to(release)): path.stat().st_mtime_ns for path in release.rglob("*") if path.is_file()}
    assert not list(release.rglob("__pycache__"))


def test_update_managed_link_keeps_old_release_and_copies_only_package_assets(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(REPOSITORY / installer.PACKAGE, source / installer.PACKAGE,
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(REPOSITORY / "LICENSE", source / "LICENSE")
    (source / installer.PACKAGE / "private.log").write_text("not for deployment")
    (source / "chat.json").write_text("not for deployment")
    prefix, bin_dir = tmp_path / "prefix", tmp_path / "bin"
    entrypoint, old_release = installer.install(source, prefix, bin_dir)
    init = source / installer.PACKAGE / "__init__.py"
    init.write_text(init.read_text() + "\n# changed package content\n")
    updated_entrypoint, new_release = installer.install(source, prefix, bin_dir)
    assert old_release != new_release and old_release.is_dir()
    assert entrypoint == updated_entrypoint
    assert entrypoint.resolve() == new_release / "mur-diagnostics"
    assert not list(new_release.rglob("*.log")) and not (new_release / "chat.json").exists()


@pytest.mark.parametrize("symlink", [False, True])
def test_preserves_unrelated_existing_executable(tmp_path, symlink):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    entrypoint = bin_dir / "mur-diagnostics"
    existing = tmp_path / "unrelated" if symlink else entrypoint
    existing.write_text("#!/bin/sh\necho unrelated\n")
    existing.chmod(0o755)
    if symlink:
        entrypoint.symlink_to(existing)
    with pytest.raises(ValueError, match="nicht überschreiben|gehört nicht"):
        installer.install(REPOSITORY, tmp_path / "prefix", bin_dir)
    assert existing.read_text() == "#!/bin/sh\necho unrelated\n"
    assert entrypoint.is_symlink() == symlink


def test_refuses_release_directory_symlink_outside_prefix(tmp_path):
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (prefix / "releases").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        installer.install(REPOSITORY, prefix, tmp_path / "bin")
    assert list(outside.iterdir()) == []


def test_refuses_individual_release_symlink_and_modified_release(tmp_path):
    prefix, bin_dir = tmp_path / "prefix", tmp_path / "bin"
    files = installer._source_files(REPOSITORY)
    release = prefix / "releases" / (installer._version(files) + "-" + installer._digest(files))
    release.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    release.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlink"):
        installer.install(REPOSITORY, prefix, bin_dir)
    release.unlink()
    _, actual_release = installer.install(REPOSITORY, prefix, bin_dir)
    (actual_release / installer.PACKAGE / "cli.py").write_text("unexpected change")
    with pytest.raises(ValueError, match="verändert"):
        installer.install(REPOSITORY, prefix, bin_dir)
