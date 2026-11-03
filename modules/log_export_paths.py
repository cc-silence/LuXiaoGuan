"""One persistent destination preference shared by every diagnostic exporter."""
from pathlib import Path
import logging
from modules.config_services.config_store import ConfigPair, user_config_dir


def _store():
    from modules.config_manager import CONFIG_DIR
    return ConfigPair(user_config_dir() / "log_export.json", CONFIG_DIR / "log_export.json")


def export_directory():
    from modules.config_manager import user_downloads_dir
    data = _store().read_newest_json() or {}
    saved = data.get("directory")
    if isinstance(saved, str) and saved:
        try:
            directory = Path(saved)
            if directory.is_absolute() and directory.is_dir():
                return directory
        except (OSError, ValueError):
            pass
    return user_downloads_dir()


def choose_log_export_path(owner, title, category):
    from PySide6.QtWidgets import QFileDialog
    from modules.log_services import build_export_filename
    picked, _ = QFileDialog.getSaveFileName(owner, title,
        str(export_directory() / build_export_filename(category)), "ZIP (*.zip)")
    if picked:
        try:
            _store().write_json(dict(directory=str(Path(picked).absolute().parent)))
        except OSError:
            logging.getLogger(__name__).warning("Could not save log export directory preference")
    return picked
