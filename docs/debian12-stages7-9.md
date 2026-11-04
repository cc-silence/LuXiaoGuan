# Debian 12: reviewed source sync, release slots, native terminal preparation

Recorded 2026-10-25. This document supersedes the architecture/source-sync pending
statements in historical stage 1-6 notes; those notes describe their original stage.
The user confirmed Intel/AMD x86-64, has no Debian installation and declined WSL.

## Stage 7: reviewed shared-source synchronization

Synced 33 later Windows source/document/test changes into the Linux worktree,
reviewing four shared-file conflicts rather than overwriting Linux behavior.
Version is 1.0.18_alpha in both snapshots. The transfer admission regression
caught a moved Windows diagnostics statement obscuring a prior exact-text merge;
the Linux updater guard was restored and its four admission tests passed.
Reports and byte backups are under build/debian12/windows-sync-*.

## Stage 8: release lifecycle

Cloud application ZIP slots: Windows stable, Windows alpha, Linux stable, Linux
alpha. Each retains one signed current complete ZIP; older eligible ZIPs are
actually removed. Shared referenced incremental blobs/catalogs and the two current
router offline packages remain protected. Future/unrecognized archives fail safe.

Normal publication keeps the active release until new public bytes and signatures
are checked. Only an insufficient-space exception for alpha can retire its own
platform's current alpha. It first proves sufficient projected capacity, saves a
recovery journal with the original signed inputs, changes that alpha to a publishing
state, rechecks the snapshot, deletes only verified same-platform alpha archives,
confirms space and uploads. Retries reuse the same signed preparation. Validation
errors never enter the quota exception. Stable and the other platform remain usable.

The gateway preserves legacy Windows /manifest and adds
/manifest?platform=linux-x64. Missing Linux slots return 204; invalid targets return
400. During alpha replacement, authorized alpha clients receive a bounded 503
publishing state; ordinary stable requests continue normally. Synthetic maintenance
cases were tested; no active channel was deliberately interrupted in production.

Actual cleanup removed the unreferenced 1.0.16_alpha ZIP (66,723,285 bytes) after
signature, local SHA-256, remote size and public ETag checks of retained 1.0.18_alpha.
Total recorded cloud usage changed from 768,193,271 to 701,469,986 bytes. Those are
snapshot values, not a continuing live quota monitor. The gateway was deployed and
four anonymous live checks passed. No testing code was sent for these checks.

These shared publisher/client-response changes were copied to the active Windows
checkout with backups. Its 13 publication/quota tests passed. This work did not
publish a new application version or build a Windows package.

## Stage 9: reusable native terminal source

The Windows terminal's existing build-manifest identified 502 source files. All
502 bytes/hashes matched the available original native source. Only those verified
files were recovered, including upstream provenance and licenses; unrelated legacy
application code, credentials and configuration were not imported.

The bridge uses Qt Q_DECL_EXPORT in place of a Windows-only export spelling. A
CMake build requires Qt 6.3.0 exactly and retains the current external SSH transport,
2000-line scrollback bound, input/clipboard behavior and rendering component.
Linux uses libroutermanager_terminal.so; Windows retains winbox_terminal.dll.
Linux checks the ELF format, x86-64 architecture and shared-library kind before
loading, and the common loader still checks the exact runtime Qt version.

The build entry runs configure/build/smoke with explicit limits, two compiler jobs
and process-group cleanup on timeout/cancellation. It never installs output until a
new library passes the real existing terminal smoke in an isolated Qt process.
Failures preserve a prior library and retain reports. It does not change system Qt,
replace Python, connect a router or create a local terminal shell.

On an actual admitted Debian/private Python host with the matching SDK:

    PRIVATE_PYTHON -m scripts.build_linux_terminal --qt-prefix /path/to/Qt-6.3.0

Use a real absolute PRIVATE_PYTHON path. No SDK download or compile was run here.
Debian 12's Qt Core package is 6.4.2, so blindly compiling against the system SDK
would violate the currently pinned application's exact Qt check.

Eight terminal admission/failure/cleanup tests pass on Windows. The existing Windows library
also passed its actual Qt output/input smoke. Neither proves Linux compilation,
Linux ABI/loading correctness or native desktop rendering.

## Maintenance and remaining gates

Python 3.10 ended support on 2026-10-01. Record a runtime upgrade review before a
production Linux release; do not silently change PySide6 or the Windows environment.
The installer, Linux-native update transaction, catalog, publisher preparation and
native environment acceptance remain unfinished. See debian12-progress.md.

References:
- https://doc.qt.io/qt-6/cmake-get-started.html
- https://doc.qt.io/qt-6/qtcompilerdetection.html#Q_DECL_EXPORT
- https://packages.debian.org/bookworm/libqt6core6
- https://www.python.org/downloads/release/python-31022/
- Native upstream revisions: native/router_terminal/upstream.json
