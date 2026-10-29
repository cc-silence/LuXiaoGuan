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
