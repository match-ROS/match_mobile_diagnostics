#!/usr/bin/env python3
"""Explicit, offline installation of the MuR diagnostic CLI using only stdlib."""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid


PACKAGE = "match_mobile_diagnostics"
MARKER = "# Managed by match_mobile_diagnostics scripts/install_cli.py"


def _source_files(source: Path):
    package = source / PACKAGE
    if not package.is_dir() or package.is_symlink():
        raise ValueError(f"Paketverzeichnis fehlt oder ist ein Symlink: {package}")
    files = {}
    for path in sorted(package.rglob("*")):
        relative = path.relative_to(source)
        parts = relative.parts
        allowed = path.suffix == ".py" or (
            len(parts) >= 3 and parts[1] in ("profiles", "knowledge")
            and path.suffix in ({".json"} if parts[1] == "profiles" else {".json", ".md"}))
        if not allowed:
            continue
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != source and source in parent.parents):
            raise ValueError(f"Quelldatei darf keinen Symlink enthalten: {path}")
        if path.is_file():
            files[str(relative)] = path.read_bytes()
    license_path = source / "LICENSE"
    if license_path.is_symlink() or not license_path.is_file():
        raise ValueError("Eine reguläre LICENSE-Datei wird benötigt.")
    files["LICENSE"] = license_path.read_bytes()
    if f"{PACKAGE}/cli.py" not in files or f"{PACKAGE}/__init__.py" not in files:
        raise ValueError("Paket enthält keinen CLI-Einstieg oder keine Versionsdatei.")
    return files


def _version(files):
    module = ast.parse(files[f"{PACKAGE}/__init__.py"].decode("utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
                return value
    raise ValueError("Keine gültige konstante Paketversion gefunden.")


def _digest(files):
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()[:12]


def _launcher():
    return ("#!/usr/bin/python3\n" + MARKER + "\n"
            "import os\nimport sys\nfrom pathlib import Path\n"
            "sys.dont_write_bytecode = True\n"
            "os.environ['PYTHONDONTWRITEBYTECODE'] = '1'\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
            "from match_mobile_diagnostics.cli import main\n"
            "raise SystemExit(main())\n").encode("utf-8")


def _check_existing_entrypoint(entrypoint: Path, releases: Path):
    if not entrypoint.exists() and not entrypoint.is_symlink():
        return
    if not entrypoint.is_symlink():
        raise ValueError(f"Vorhandenen, nicht verwalteten CLI-Einstieg nicht überschreiben: {entrypoint}")
    try:
        target = entrypoint.resolve(strict=True)
        target.relative_to(releases)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Vorhandener CLI-Symlink gehört nicht zu diesem Installationspräfix: {entrypoint}") from exc
    if target.name != "mur-diagnostics" or not target.is_file() or MARKER not in target.read_text(encoding="utf-8"):
        raise ValueError(f"Vorhandenen, nicht verwalteten CLI-Symlink nicht überschreiben: {entrypoint}")


def _verify_release(release: Path, files):
    if release.is_symlink() or not release.is_dir():
        raise ValueError(f"Release-Ziel ist kein reguläres Verzeichnis: {release}")
    actual = set()
    for path in release.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Release enthält einen unerwarteten Symlink: {path}")
        if path.is_file():
            actual.add(str(path.relative_to(release)))
    if actual != set(files):
        raise ValueError(f"Vorhandenes Release enthält abweichende Dateien: {release}")
    for name, content in files.items():
        if (release / name).read_bytes() != content:
            raise ValueError(f"Vorhandenes Release wurde verändert: {release / name}")
    if not os.access(release / "mur-diagnostics", os.X_OK):
        raise ValueError(f"Vorhandener Release-Einstieg ist nicht ausführbar: {release}")


def install(source: Path, prefix: Path, bin_dir: Path):
    source = source.expanduser().resolve()
    prefix = prefix.expanduser().absolute()
    bin_dir = bin_dir.expanduser().absolute()
    files = _source_files(source)
    release_name = _version(files) + "-" + _digest(files)
    files["mur-diagnostics"] = _launcher()
    prefix.mkdir(parents=True, exist_ok=True)
    prefix = prefix.resolve()
    releases = prefix / "releases"
    if releases.is_symlink():
        raise ValueError(f"Release-Verzeichnis darf kein Symlink sein: {releases}")
    releases.mkdir(exist_ok=True)
    release = releases / release_name
    if release.is_symlink():
        raise ValueError(f"Release-Ziel darf kein Symlink sein: {release}")
    bin_dir.mkdir(parents=True, exist_ok=True)
    entrypoint = bin_dir / "mur-diagnostics"
    _check_existing_entrypoint(entrypoint, releases)

    if release.exists():
        _verify_release(release, files)
    else:
        staging = Path(tempfile.mkdtemp(prefix=".install-", dir=releases))
        try:
            for name, content in files.items():
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            (staging / "mur-diagnostics").chmod(0o755)
            staging.rename(release)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    _check_existing_entrypoint(entrypoint, releases)
    temporary = bin_dir / (".mur-diagnostics-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to(release / "mur-diagnostics")
        os.replace(temporary, entrypoint)
    finally:
        if temporary.is_symlink():
            temporary.unlink()
    return entrypoint, release


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1], help="Repository mit Paket und LICENSE")
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local/share/mur-diagnostics", help="Verzeichnis für versionierte Releases")
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local/bin", help="Verzeichnis des CLI-Symlinks")
    args = parser.parse_args(argv)
    try:
        entrypoint, release = install(args.source, args.prefix, args.bin_dir)
    except (OSError, ValueError, SyntaxError) as exc:
        print(f"Installation abgebrochen: {exc}", file=sys.stderr)
        return 1
    print(f"CLI installiert: {entrypoint}\nRelease: {release}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
