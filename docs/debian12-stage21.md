# Debian 12 parity review - 2026-10-30

Target: Debian 12, Intel/AMD x86-64. Product version: 1.0.27_alpha.
The Windows source working snapshot was reviewed without changing its source files.

## Changes

- Synchronized the current automatic capture, login/install, read-only probe cleanup,
  wireless state, log history, window lifecycle and shared UI changes.
- Linux now downloads the signed complete ZIP, preserving internal file links and
  Unix executable permissions. The independent installer derives its inventory
  again from verified ZIP bytes and rejects modified cached inventories.
- Kept the existing transaction journal, old-runtime snapshot, parent identity,
  rollback, recovery launcher, private keyring and user-data protection.
- Preserved the catalog-hash binding used by the durable transaction: download
  stages are atomically handed over to their derived inventory identity.
- Added bounded resume, ready-cache reuse, backup-space admission and authenticated
  obsolete-partial cleanup. Unknown files and active recovery stages are preserved.
- Formal and alpha publishing use independent Windows/Linux slots. Ordinary Linux
  publishing produces only the complete ZIP and signed notification, with no remote
  blobs/catalog objects. A legacy bridge remains Windows-only.
- Linux packages must include the automatic-capture shell script and offline-tool
  files under bin/tools. Missing or unversioned release assets stop the build.
- Native terminal loading validates ELF architecture before loading code. Recovery
  selects its OS backend before GUI startup.
- Added exact source-snapshot checks, including native code and new files, and made
  full-package checks mandatory in Debian CI and native package admission.

## Verification

The consolidated report is build/debian12/stage21-sync/verified-summary.json.

- 49 selected Python test modules: 486 executed cases passed; 25 native-only cases skipped on Windows. Counts include imported fixture cases.
- Windows offscreen eight-page/settings/exit integration passed in isolation.
  An earlier parallel run timed out; isolated Windows-mainline and Linux-worktree
  rechecks both passed. This is not Debian GUI acceptance.
- All 16 local update-gateway tests passed; no gateway was deployed.
- Repository policy and whitespace checks passed. Modified Python files were
  compiled and checked for UTF-8 replacement characters and merge markers.
- Read and SHA256-verified 250 cached official source files across 50 OpenWrt
  releases from the upstream compatibility audit. Router commands remain shared
  with that reviewed source; this is not new physical-router certification.
- Python ZIP/dirfd references read for this implementation:
  https://docs.python.org/3.10/library/zipfile.html
  https://docs.python.org/3.10/library/os.html#files-and-directories

## Remaining native acceptance

No Debian build, Linux desktop, real keyring, real updater binary pair or router
acceptance was performed. Ten native updater modules must pass with zero skips
before candidate admission. X11/Wayland, tray, fonts, scale, upgrade/rollback and
interrupted recovery still need the actual Debian environment.

The Windows transaction repair remains deferred by the user. No repair, restart,
cloud publication, commit, push or pull request was performed in this task.
