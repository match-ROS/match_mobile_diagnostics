"""Small, JSON-native result contract shared by every frontend."""
from collections import Counter
from datetime import datetime, timezone
import re

from . import __version__

STATUSES = ("pass", "warn", "fail", "unknown", "not_applicable")
LABELS = dict(zip(STATUSES, ("Bestanden", "Hinweis", "Fehler", "Nicht prüfbar", "Nicht zutreffend")))


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact(value):
    """Never export common credential representations from diagnostic evidence."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if re.search(r"password|passwd|authorization|api[_-]?key|token|secret", k, re.I)
                    else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        # Consume a complete authentication scheme and credential together.
        value = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:(?:Basic|Bearer)\s+)?\S+", r"\1[REDACTED]", value)
        value = re.sub(r"(?i)(\b(?:Basic|Bearer)\s+)\S+", r"\1[REDACTED]", value)
        value = re.sub(r"(?i)((?:password|passwd|api[_-]?key|token|secret)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", value)
        return re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", value)
    return value


def result(check_id, component, status, summary, *, expected=None, actual=None,
           source="", evidence=None, causes=None, next_steps=None, knowledge_id="",
           age_seconds=None, observed_at=None):
    if status not in STATUSES:
        raise ValueError(f"Invalid check status: {status}")
    return redact(dict(id=check_id, component=component, status=status, summary=summary,
                       expected=expected, actual=actual, source=source,
                       observed_at=observed_at or utc_now(), age_seconds=age_seconds,
                       evidence=evidence or [], causes=causes or [],
                       next_steps=next_steps or [], knowledge_id=knowledge_id))


def finalize(report):
    checks = report.get("results")
    if not isinstance(checks, list) or any(
        not isinstance(item, dict) or item.get("status") not in STATUSES for item in checks
    ):
        raise ValueError("Ungültige Diagnoseergebnisse oder unbekannter Checkstatus")
    counts = Counter(item["status"] for item in checks)
    report["counts"] = {status: counts[status] for status in STATUSES}
    applicable = len(report["results"]) - counts["not_applicable"]
    report["complete"] = counts["unknown"] == 0 and applicable > 0
    if counts["fail"]:
        summary = f"{counts['fail']} Fehler, {counts['warn']} Hinweise, {counts['unknown']} nicht prüfbare Checks."
    elif counts["unknown"] or applicable == 0:
        summary = f"Diagnose unvollständig: {counts['unknown']} nicht prüfbare Checks, {counts['warn']} Hinweise."
    elif counts["warn"]:
        summary = f"Keine bestätigten Fehler; {counts['warn']} Hinweise prüfen."
    else:
        summary = f"Alle vorgesehenen automatischen Checks bestanden ({counts['pass']}/{applicable})."
    report["summary"] = summary
    return redact(report)


def new_report(robot, namespace, mode, target):
    return dict(schema_version=1, tool_version=__version__, robot=robot, namespace=namespace,
                mode=mode, target=target, checked_at=utc_now(), complete=False,
                summary="Diagnose läuft", counts={}, results=[], stats={}, manual_observations=[])


def exit_code(report):
    if report["counts"].get("fail") or report["counts"].get("warn"):
        return 1
    return 0 if report["complete"] else 2
