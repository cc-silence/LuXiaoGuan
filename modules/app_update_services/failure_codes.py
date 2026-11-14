"""Classify known validation failures without exporting arbitrary exception text."""
import errno

_EXACT = {
    'Catalog fingerprint mismatch': 'catalog_hash_mismatch',
    'Downloaded object fingerprint mismatch': 'download_hash_mismatch',
    'Decompressed file fingerprint mismatch': 'expanded_file_hash_mismatch',
    'Decompressed file exceeds signed size': 'expanded_size_exceeded',
    'Backup verification failed': 'backup_hash_mismatch',
    'Installed file fingerprint mismatch': 'installed_hash_mismatch',
    'Rollback backup is damaged': 'rollback_backup_damaged',
    'File changed during update preparation': 'file_changed_during_preparation',
    'Staging identity differs from signature': 'staging_identity_mismatch',
    'Not enough disk space for download and rollback': 'insufficient_disk_space',
    'Another update is downloading or installing': 'cache_in_use',
    'Invalid signed manifest envelope': 'invalid_manifest_envelope',
    'Unsupported RouterManager update manifest': 'invalid_product_or_platform',
    'Unsupported LuXiaoGuan update manifest': 'invalid_product_or_platform',
    'Update URL must use HTTPS without credentials': 'unsafe_update_url',
    'Invalid update URL': 'invalid_update_url',
    'Invalid target catalog': 'invalid_target_catalog',
    'History target mismatch': 'catalog_history_target_mismatch',
    'Invalid update network mode': 'invalid_network_mode',
}
_PREFIX = {
    'Edited file conflicts:': 'local_file_conflicts',
    'Staged file is missing or damaged:': 'staged_file_damaged',
    'Configuration is damaged;': 'local_configuration_damaged',
    'New application startup validation failed:': 'startup_validation_failed',
    'Linked update target:': 'linked_target_rejected',
    'Unsafe managed path:': 'unsafe_target_path',
    'Protected user path:': 'protected_user_path',
    'File changed while checking update:': 'file_changed_during_scan',
}


_MESSAGES = {
    'catalog_hash_mismatch': '更新清单校验失败，已停止更新。请重试。',
    'download_hash_mismatch': '下载文件校验失败，请重新下载。',
    'expanded_file_hash_mismatch': '解压文件校验失败，已停止更新。',
    'expanded_size_exceeded': '更新包解压后大小异常，已停止更新。',
    'backup_hash_mismatch': '更新备份校验失败，已停止更新。',
    'installed_hash_mismatch': '安装文件校验失败，已回滚。',
    'rollback_backup_damaged': '回滚备份已损坏，请重新下载更新包。',
    'file_changed_during_preparation': '更新期间文件被修改，请关闭相关程序后重试。',
    'staging_identity_mismatch': '更新包来源校验失败，已拒绝。',
    'insufficient_disk_space': '磁盘空间不足，请清理后重试。',
    'cache_in_use': '已有更新正在下载或安装，请稍后再试。',
    'invalid_manifest_envelope': '更新清单格式错误，已拒绝。',
    'invalid_product_or_platform': '该更新包不适用于当前产品或系统。',
    'unsafe_update_url': '更新地址不安全，已拒绝。',
    'invalid_update_url': '更新地址无效，请检查网络设置。',
    'invalid_target_catalog': '更新清单与当前软件不匹配。',
    'catalog_history_target_mismatch': '更新历史与清单不匹配。',
    'invalid_network_mode': '更新网络模式无效，请检查设置。',
    'local_file_conflicts': '本地文件与更新冲突，请先处理后再更新。',
    'staged_file_damaged': '暂存文件缺失或损坏，请重新下载。',
    'local_configuration_damaged': '本地配置损坏，请修复后重试。',
    'startup_validation_failed': '更新后程序启动校验失败，已回滚。',
    'linked_target_rejected': '更新目标包含链接，已拒绝。',
    'unsafe_target_path': '更新目标路径不安全，已拒绝。',
    'protected_user_path': '更新涉及受保护的用户目录，已拒绝。',
    'file_changed_during_scan': '检查更新期间文件发生变化，请重试。',
    'signature_invalid': '更新包签名验证失败，已拒绝。',
    'permission_denied': '没有权限写入更新目录。',
    'file_missing': '更新所需文件不存在，请重新检查更新。',
    'timeout': '更新请求超时，请重试。',
    'disk_full': '磁盘空间不足，请清理后重试。',
    'invalid_json': '更新清单格式错误，已拒绝。',
    'cancelled': '更新已取消。',
}


def failure_text(exc):
    """把已知更新校验失败翻译成客户能看懂的结论，未知细节只进日志。"""

    code = failure_code(exc)
    if code in _MESSAGES:
        return _MESSAGES[code]
    from modules.user_errors import plain_exception
    return plain_exception(exc)


def failure_code(exc):
    if type(exc).__name__ == 'InvalidSignature':return 'signature_invalid'
    if isinstance(exc, PermissionError):return 'permission_denied'
    if isinstance(exc, FileNotFoundError):return 'file_missing'
    if isinstance(exc, TimeoutError):return 'timeout'
    if isinstance(exc, InterruptedError):return 'cancelled'
    if getattr(exc,'errno',None) == errno.ENOSPC:return 'disk_full'
    if type(exc).__name__ == 'JSONDecodeError':return 'invalid_json'
    text=str(exc)
    if text in _EXACT:return _EXACT[text]
    for prefix, code in _PREFIX.items():
        if text.startswith(prefix):return code
    return ''
