"""Human-readable rendering of the same report consumed by agents."""
import json
from .models import LABELS, redact


def display(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value if value is not None else "—")


def markdown_report(report):
    report = redact(report)
    lines = [f"# MuR-Diagnose: {report['robot']}", "", report["summary"], "",
             f"Zeit: {report['checked_at']} · Ziel: {report['target']} · Modus: {report['mode']}",
             f"Namespace: {report.get('namespace') or 'nicht ermittelt'} · Tool: {report['tool_version']}", ""]
    for item in report["results"]:
        lines += [f"## {LABELS.get(item['status'], item['status'])}: {item['summary']}", "",
                  f"ID: `{item['id']}` · Quelle: {item.get('source', '')}",
                  f"Soll: {display(item.get('expected'))}", f"Ist: {display(item.get('actual'))}", ""]
        for key, label in (("evidence", "Beleg"), ("causes", "Mögliche Ursache"), ("next_steps", "Nächster Schritt")):
            lines.extend(f"- {label}: {display(text)}" for text in item.get(key, []))
        lines.append("")
    if report.get("manual_observations"):
        lines += ["## Nutzerangaben (nicht automatisch verifiziert)", ""]
        for note in report["manual_observations"]:
            lines.append(f"- {note.get('observed_at', '')} · {note.get('robot', report['robot'])}: {note.get('text', '')}")
        lines.append("")
    return "\n".join(lines)
