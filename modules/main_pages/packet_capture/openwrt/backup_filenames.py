"""Shared default names for local router configuration backups."""
from datetime import datetime
import re


def backup_filename(operation=None, *, now=None):
    title = "配置备份" if not operation else "执行" + str(operation) + "前备份"
    return title + "_" + (now or datetime.now()).strftime("%Y%m%d_%H%M%S") + ".tar.gz"


def dependency_backup_filename(device=None, *, now=None):
    def part(value, fallback):
        value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]+', '_', str(value or ''))
        return value.strip(' ._').encode('utf-8')[:60].decode('utf-8', errors='ignore').rstrip(' ._') or fallback

    model = part(getattr(device, 'model', '') or getattr(device, 'board_name', ''), '未知型号')
    system = part(getattr(device, 'distribution', ''), '未知系统')
    version = part(getattr(device, 'version', ''), '未知版本')
    stamp = (now or datetime.now()).strftime('%Y%m%d_%H%M%S')
    return '路小管依赖备份_' + model + '_' + system + '_' + version + '_' + stamp + '.zip'
