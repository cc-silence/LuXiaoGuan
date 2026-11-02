"""Current native runtime state, separate from saved wireless configuration."""
from dataclasses import dataclass, field, replace
from .readiness_diagnostics import readiness_evidence
import re


@dataclass(frozen=True)
class RuntimeObservation:
    section: str
    radio: str
    mode: str
    running: str
    note: str = ""
    readiness: dict = field(default_factory=dict)


def parse_observations(values):
    if values == {}:
        values = []
    if not isinstance(values, list) or len(values) > 256:
        raise ValueError("Invalid wireless runtime observations")
    result = []
    seen = set()
    for row in values:
        if not isinstance(row, dict):
            raise ValueError("Invalid wireless runtime observation")
        section, radio = row.get("section"), row.get("radio")
        if any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", v) for v in (section,radio)):
            raise ValueError("Invalid wireless runtime identity")
        mode, running, note = row.get("mode"), row.get("running"), row.get("note", "")
        if mode not in ("ap", "sta") or running not in ("active", "inactive", "unknown") or note not in ("", "pending", "setup_failed"):
            raise ValueError("Invalid wireless runtime state")
        if section in seen:
            raise ValueError("Duplicate wireless runtime observation")
        seen.add(section)
        result.append(RuntimeObservation(section,radio,mode,running,note,readiness_evidence(row.get("readiness"))))
    return tuple(result)


def merge_runtime(inventory, observations):
    current = {(v.section,v.radio,v.mode): v for v in observations}
    notes = {"": "", "pending": "\u65e0\u7ebf\u7f51\u5361\u6b63\u5728\u91cd\u65b0\u52a0\u8f7d", "setup_failed": "\u56fa\u4ef6\u62a5\u544a\u65e0\u7ebf\u521d\u59cb\u5316\u5931\u8d25"}
    rows = []
    changed = False
    for row in inventory.rows:
        value = current.get((row.section,row.radio,row.mode))
        if value is not None and (row.running != value.running or row.runtime_note != notes[value.note] or row.readiness != value.readiness):
            row = replace(row,running=value.running,runtime_note=notes[value.note],readiness=value.readiness)
            changed = True
        rows.append(row)
    return replace(inventory,rows=tuple(rows)) if changed else inventory
