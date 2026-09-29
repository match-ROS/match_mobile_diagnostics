"""Public command line; machine-readable stdout, operational messages on stderr."""
import argparse
import json
import math
from pathlib import Path
import signal
import sys

from . import __version__
from .models import exit_code, redact
from .profiles import ROBOTS
from .reports import markdown_report
from . import processes


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or not 0.1 <= number <= 60:
        raise argparse.ArgumentTypeError("Zeitwert muss zwischen 0.1 und 60 Sekunden liegen")
    return number


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(3, f"{self.prog}: {message}\n")


def parser():
    root = ArgumentParser(description="Lesende Diagnose für MuR620-Hardware und Treiber")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("scan", "watch"):
        sub = commands.add_parser(name, help="Einzeldiagnose" if name == "scan" else "Laufende Beobachtung")
        sub.add_argument("--robot", choices=ROBOTS, required=True)
        sub.add_argument("--via", choices=("local", "ssh"), default="local")
        sub.add_argument("--host", help="SSH-Alias oder user@host; Standard: Robotername")
        sub.add_argument("--domain-id", type=int, choices=range(233), metavar="0..232", help="Beobachtungsdomain; Standard aus Sollprofil (62)")
        sub.add_argument("--namespace", help="Expliziter ROS-Namespace, unabhängig vom physischen Roboter")
        sub.add_argument("--workspace", help="Workspace auf dem Roboter-PC")
        sub.add_argument("--mode", choices=("operational", "preflight"), default="operational")
        sub.add_argument("--duration", type=positive_seconds, default=10.0, help="Erstes ROS-Beobachtungsfenster (s)")
        sub.add_argument("--format", choices=("json", "jsonl", "markdown"), default="jsonl" if name == "watch" else "markdown")
        sub.add_argument("--output", type=Path, help="Bericht zusätzlich in Datei schreiben (enthält lokale Diagnosedaten)")
        if name == "watch":
            sub.add_argument("--interval", type=positive_seconds, default=2.0)
    knowledge = commands.add_parser("knowledge", help="Offline-Wissensbasis")
    knowledge.add_argument("action", choices=("list", "show"), nargs="?", default="list")
    knowledge.add_argument("article", nargs="?")
    knowledge.add_argument("--format", choices=("json", "markdown"), default="markdown")
    commands.add_parser("gui", help="PyQt5-Oberfläche starten")
    return root


def _cancel(_signum, _frame):
    processes.stop_all()
    raise KeyboardInterrupt


def _emit(report, format_name, output=None):
    report = redact(report)
    text = markdown_report(report) if format_name == "markdown" else json.dumps(report, ensure_ascii=False,
                allow_nan=False, indent=2 if format_name == "json" else None)
    print(text, flush=True)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        # A watch export is the latest full snapshot, not an ever-growing unbounded log.
        output.write_text(text + "\n", encoding="utf-8")


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "gui":
        from .gui import main as gui_main
        return gui_main([])
    if args.command == "knowledge":
        from .knowledge import list_articles, get_article
        if args.action == "show":
            if not args.article:
                parser().error("knowledge show benötigt eine Artikel-ID")
            try:
                article = get_article(args.article)
            except KeyError:
                parser().error("Unbekannte Artikel-ID: " + args.article)
            print(json.dumps(article, ensure_ascii=False, indent=2) if args.format == "json" else article["body"])
        else:
            articles = list_articles()
            print(json.dumps(articles, ensure_ascii=False, indent=2) if args.format == "json" else
                  "\n".join(f"{entry['id']}: {entry['title']}" for entry in articles))
        return 0
    if args.namespace:
        from .ros_collector import normalize_namespace
        try:
            args.namespace = normalize_namespace(args.namespace)
        except ValueError as exc:
            parser().error(str(exc))
    if args.command == "watch" and args.format == "json":
        parser().error("watch verwendet JSONL: --format jsonl")
    signal.signal(signal.SIGTERM, _cancel)
    signal.signal(signal.SIGINT, _cancel)
    from .engine import scan, watch
    options = {key: getattr(args, key) for key in ("robot", "via", "host", "namespace", "workspace", "mode", "duration", "domain_id")}
    try:
        if args.command == "scan":
            report = scan(**options)
            _emit(report, args.format, args.output)
            return exit_code(report)
        for report in watch(interval=args.interval, **options):
            _emit(report, args.format, args.output)
        return 0
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
    except (OSError, ValueError) as exc:
        print(f"Diagnose konnte nicht ausgeführt werden: {redact(str(exc))}", file=sys.stderr)
        return 3
    finally:
        processes.stop_all()


if __name__ == "__main__":
    raise SystemExit(main())
