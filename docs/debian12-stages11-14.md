# Debian 12 source, runtime and updater preparation

Checked: 2026-10-28. Target: Debian 12 desktop, Intel/AMD x86-64.
All work in this document is local and isolated from active Windows source.
No Debian environment, Linux binary, installer or live update is certified here.

## Reviewed source synchronization

The recorded committed Windows baseline is 1.0.21_alpha at
f38c3fcf2a9ffd2d8d047b2fcbf40c33ef39bd8a.
173 distinct files were synchronized in two reviewed waves, with backups and
fingerprints retained under build/debian12/stage11-sync and stage13-sync.
Manual conflict resolutions retained Linux credentials, no-replace publication,
result-reference metadata, native terminal selection and updater admission guards.
Current committed focus/theme, safe remote file deletion and policy cleanup fixes
are included. No Windows executable is treated as a Linux component.

Run from this worktree:

```powershell
python scripts/check_platform_sync.py --upstream C:/Users/CC/Desktop/Work/RouterManager/RouterManager_python --report build/debian12/alignment.json
```

This guard compares the actual commit as well as the application version. Pending
Windows edits are filenames only, separately reported and never applied. The
marker docs/debian12-source-sync.json records reviewed committed integration; it
does not certify native Linux behavior or unfinished Windows development.

## Private interpreter and manual CI

scripts/prepare_linux_python.py accepts the existing candidate CPython 3.10.22
only from the fixed official tar.xz fingerprint. The approximately 20 MB original
archive was obtained and verified; safe member extraction was exercised on
Windows and its temporary extracted tree removed. Native compilation was not run.

The native builder requires actual Debian 12 x86-64, checks all archive members
before extraction, uses a private versioned prefix and make altinstall, and
publishes its current pointer only after exact interpreter/native import checks.
It never replaces system Python. Build phases have bounded timeouts and process
cleanup. An earlier verified pointer survives a failed new build.

```text
python scripts/prepare_linux_python.py --plan
python3 -m scripts.prepare_linux_python --source /absolute/path/Python-3.10.22.tar.xz
```

The private build prefix is for freezing an application; it is not itself a
relocatable end-user installer. The native terminal still needs the separate exact
Qt 6.3.0 SDK and scripts/build_linux_terminal.py validation.

.github/workflows/debian12-platform.yml is a manual-only, read-only preparation
workflow in a disposable Debian 12 container. It builds private Python, checks
pinned imports and ten selected platform test modules. It has not been uploaded
or dispatched. It neither publishes a release nor installs a system here. Container
checks cannot validate the actual desktop, keyring, tray, terminal or router.

## Linux update inventory

modules/app_update_services/linux_catalog.py and scripts/linux_update_catalog.py
use explicit Linux schema 2. Inventory checks preserve Linux case sensitivity,
0644/0755 permissions, and bounded relative file links resolving to managed files.
They reject Windows binaries, protected user paths, traversal, special files,
link cycles, linked directory parents and unimplemented configuration migrations.
Catalog fingerprints and target identity remain bound to the signed release.
Real Ed25519 signature tests use synthetic package bytes; nothing is executed.

Windows schema 1 behavior is preserved. Shared history validation was extracted
without changing the Windows managed-path policy. The live publishing application
still selects Windows; the new Linux artifact function is not a published update.

## Stable old-runtime preparation

modules/platform_services/linux_update_snapshot.py prepares the current runtime
under a validated private update stage before a future installer replaces files.
It checks every byte and executable mode, requires a Linux x86-64 ELF header, and
uses hardlinks where possible to avoid duplicating large runtime files. A bounded
streaming copy is used only when hardlinks are unsupported. Catalog depth, member
count and directory count are bounded. Cancellation/timeouts and durable file,
directory and record writes are included.

The snapshot survives atomic replacement of live files. Future installation must
replace files atomically and never truncate their hardlinked old inodes. This
component starts no processes and never writes installed application files.
It does not implement install transactions, rollback or interrupted-stage repair.
Those components and a stable updater launch protocol remain required.

Windows tests model Unix metadata/no-follow/fsync where unavailable; real NTFS
hardlink/copy behavior is exercised. The manual Debian workflow additionally runs
these cases with native Linux metadata and no-follow operations, if dispatched.
The synthetic ELF header is never launched and is not evidence of a built app.

## Current evidence and remaining work

- stage14-final-platform/summary.json: 10 modules, 94 collected, 92 executed
  passes, two explicit native Linux skips under Windows.
- stage13-sync/review/summary.json: 8 modules, 83 passed at that revision.
- stage12-update-contract/summary.json: 12 modules, 94 collected, 93 executed
  passes, one native Linux skip.
- stage11-sync/configuration-review: 75 passed; storage-review: 78 passed.
- The monitor creation subprocess previously timed out while extraction was
  active. No business timer or subprocess timeout was changed; monitor-recheck
  passed all 11 cases on the unchanged source in isolation.

Remaining source work includes native package/installer integration, Linux
publisher preparation, update worker execution, transactional replacement,
rollback, crash reconciliation and preservation of user files. Real Debian is
still required for runtime/terminal compilation and desktop/install/update/device
acceptance. Updating the Python generation remains deferred by user choice.
