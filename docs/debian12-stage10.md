# Debian 12 stage 10: capture result references and local evidence

Recorded 2026-10-25. Scope is the desktop filesystem, not router firmware or radio
behavior. No SSH commands, OpenWrt package dependencies or device settings changed.
The user deferred the Python/runtime upgrade; keep the accepted private Python
3.10 candidate and PySide6 6.3.0 while continuing this work. No WSL installation.

## Root cause and public interfaces

The result evidence service already retained unknown references and cleared a
capture completion marker only when every known artifact was confirmed missing.
However, its path resolver unconditionally applied Windows separators/drive rules,
and its metadata checker returned unknown for all non-Windows callers. Merely
adding Linux stat calls would leave Linux references unresolvable.

Move lexical path differences into platform_services/result_paths.py, keeping the
config-service public functions and their bounded history contract. Windows drive,
UNC, tokens and relative-base behavior stay intact. Linux preserves absolute POSIX
paths and case, supports the existing explicit tokens with slash separators, uses
the most specific token root, and rejects traversal or foreign Windows references.
Unresolved old references remain unknown clues, never guessed to another directory.

Linux metadata runs in a separate application helper with the existing cancellable,
time-limited process capture. No Qt imports or router connection are required in
the helper. Only up to 64 explicit paths are checked per Linux batch, sharing the
caller's total deadline and bounded output. The existing Windows batch sizing and
PowerShell metadata query remain unchanged.

The Linux helper opens directories relative to retained descriptors with no-follow
flags, queries metadata without reading result contents, rechecks directory identity
before reporting missing, and closes all owned descriptors. It identifies mounts
using proc mount IDs and a bounded table. Known local filesystem types are eligible;
network, FUSE, overlay, unknown mounts, links, special files, permission failures and
changed directories remain unknown. A changed mount table invalidates the entire
batch. No file is deleted by this operation. An unsupported storage location can
still be used for capture files; its evidence is conservatively retained until the
user verifies or clears the record through the existing workflow.

## Verification

- Four targeted modules: 37 collected, 35 actually passed, two native-Linux skips
  on the Windows host. These counts do not claim Debian execution.
- Fifteen result-path/policy/helper tests ran, covering Linux case/token/base rules,
  Windows lexical compatibility, changed directories/mount tables, symlinks,
  permissions, malformed output, timeout/cancel and bounded batches.
- The real evidence aggregation call retains a present or uncertain artifact and
  marks only all-missing evidence eligible for clearing. Foreign paths stay unknown.
- A native integration case is ready for an actual local Debian filesystem:

    PRIVATE_PYTHON -m unittest tests.test_linux_result_presence.NativeLinuxPresenceTests -v

It checks existing, missing and symlinked files through the actual helper, verifies
original bytes remain unchanged, and explicitly skips unsupported storage. Such a
skip is not native acceptance. The CI skip allowance is Windows-only, so Linux
acceptance cannot pass the complete audit by silently skipping this integration.

Evidence: build/debian12/stage10-result-presence/summary.json and module logs.
New/changed Python files compiled immediately and text was checked for UTF-8 errors.
No active Windows source or package was modified in this stage.

## Still unfinished

Linux package/installer construction, private-runtime provisioning, actual terminal
compilation, native update installation/rollback/recovery, Linux catalog/publication
preparation and end-to-end Debian desktop/device acceptance remain pending. Cloud
platform/channel routing and retention are completed server-side prerequisites,
not proof that Linux updates can already be installed.

References checked for the filesystem implementation:
- https://docs.python.org/3.10/library/os.html
- https://man7.org/linux/man-pages/man5/proc_pid_mountinfo.5.html
- https://man7.org/linux/man-pages/man5/proc_pid_fdinfo.5.html
