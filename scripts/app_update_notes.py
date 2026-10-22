"""Release-note policy shared by full and resource publications."""
from pathlib import Path
from modules.app_update_services.manifest import version_key


def release_notes(version, notes_file=None):
    """Alpha tests have no notes; other releases require user-supplied text."""
    if version_key(version)[1] == 1:
        return ""
    if notes_file is None:
        raise ValueError("Provide user-authored UTF-8 release notes for non-alpha releases")
    path = Path(notes_file)
    if path.stat().st_size > 64000:
        raise ValueError("Release notes file is too large")
    notes = path.read_text(encoding="utf-8").strip()
    if not notes:
        raise ValueError("Provide user-authored UTF-8 release notes for non-alpha releases")
    return notes


def clean_history(history):
    """Clear alpha prose without changing version or file migration records."""
    return [dict(item, notes="" if version_key(item["version"])[1] == 1 else item["notes"])
            for item in history]
