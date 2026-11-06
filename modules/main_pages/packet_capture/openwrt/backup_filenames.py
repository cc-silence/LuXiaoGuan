"""Shared default names for local router configuration backups."""
from datetime import datetime


def backup_filename(operation=None, *, now=None):
    title = "配置备份" if not operation else "执行" + str(operation) + "前备份"
    return title + "_" + (now or datetime.now()).strftime("%Y%m%d_%H%M%S") + ".tar.gz"
