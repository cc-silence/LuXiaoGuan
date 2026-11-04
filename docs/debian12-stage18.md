# Debian 12 stage 18: current source synchronization and Qt 6.3.2

## Scope and baseline

The reviewed Linux source baseline is Windows commit
`aed7da0afa1a205de21aa4f6301c6c7cc8f2ce55`, application version 1.0.22_alpha.
The source guard reports aligned-to-committed-baseline with no pending upstream
source at the final recorded check. Future upstream commits require another review.

The main synchronization covers 79 source/document/test/theme paths from f38c3fcf
to merge commit 4ec177f5. Two Windows updater binary/provenance paths were excluded.
An additional two-path follow-up imports the publisher's configured-proxy-only
upload fix. No upload, cloud cleanup, native build, commit or push was performed.

Three-way comparison preserved Linux main-process worker routing, credentials,
startup recovery, native paths and platform services. Shared capture/settings,
LAN recovery, diagnostic evidence, restore/reset and input validation fixes now
match the committed Windows baseline. The user explicitly approved matching
input-error borders and factory-reset reconnection dialogs on both platforms.
The source-sync inventory now counts 224 distinct paths from actual applied
records, excluding unapplied candidates; these are not 224 changes in this stage.

## Dependency contract

Shared requirements now pin PySide6 6.3.2. Linux retains private CPython 3.10.22+
as previously accepted; Debian's system Python is not replaced. The minimum
private-runtime security patch admission remains unchanged.

The official metadata for PySide6, Essentials, Addons and shiboken6 at 6.3.2
contains x86-64 manylinux_2_28 cp36-abi3 wheels and Python >=3.6,<3.11 requirements.
This supports retaining the private Python 3.10 candidate; it does not certify
native installation or GUI behavior. Names, sizes and hashes are retained in
build/debian12/stage18-sync/qt-wheel-metadata.json. The Linux wheels were not
installed or executed on Windows.

The Linux reviewed Qt version is one profile constant. The terminal builder
checks the actual binding and loaded Qt against the shared dependency pin,
passes the exact version to CMake, and records it in terminal provenance.
Candidate builds reject terminal evidence from Qt 6.3.0. CMake has no unversioned
system-Qt fallback. A matching native Qt 6.3.2 development SDK must still be
provisioned and validated on Debian; none was downloaded or compiled here.

The shared packaging contract rejects mismatched binding/Qt versions and
ambiguous dependency pins before packaging. Tests cover each mismatch and
rejection of an old Linux dependency profile and stale terminal provenance.

Primary metadata and archive references:
- https://pypi.org/pypi/PySide6/6.3.2/json
- https://pypi.org/pypi/PySide6-Essentials/6.3.2/json
- https://pypi.org/pypi/PySide6-Addons/6.3.2/json
- https://pypi.org/pypi/shiboken6/6.3.2/json
- https://download.qt.io/archive/qt/6.3/6.3.2/

## Verification and limitations

A separate Windows test environment contains Qt/PySide6 6.3.2 and lupa 2.8;
Windows application files and global Python dependencies were not modified.
Its host Python is 3.10.11, suitable only for these Windows offline checks,
not admitted as the Debian private runtime.

The latest selected results comprise 36 modules, 285 collected cases, all passed
with zero skips. Imported cases can be collected in more than one module; this
is a bounded affected-area audit, not a unique full-repository test count.
A further rerun of the affected factory-reset/launcher tests passed after
introducing the portable test shell helper. The summary keeps their latest
results, without adding duplicate reruns to the count.

The new factory-reset shell test previously hardcoded Git Bash on Windows.
It now uses native /bin/bash on Linux and an explicit Git Bash executable on
Windows. The stable-launcher tests reuse the same helper with /bin/sh on Linux.
Windows never searches PATH for bash or starts WSL as a side effect.
Manual-only Debian CI now selects 22 modules, including the runtime contract,
portable shell and reset recovery cases. No workflow was dispatched.

Actual offscreen rendering covers error borders and reset recovery windows in
light/dark themes at 100/150 percent scaling. Synthetic adapter fixtures verify
that opening a recovery window does not renew a real adapter and closing it
revokes its offer. Screenshots/report are under stage18-sync/visual. The initial
render fixture did not update saved theme configuration; it was corrected to
select each theme through temporary isolated configuration. These images are
Windows offscreen evidence, not Debian fonts, desktop or input-method acceptance.

Evidence is under build/debian12/stage18-sync, including source backups, merge
plan, applied inventory, upload follow-up, metadata, summary and alignment JSON.
Underlying module logs are in the five stage18-*-audit directories.

Linux live auto-update remains disabled. Download/planning/controller routing,
shutdown coordination, progress/error presentation, worker-aware runtime-cache
retention, Linux publishing preparation and installation/uninstall experience
remain to be completed. Native compilation, frozen startup/update/rollback,
keyring and authorized device acceptance still require a real Debian environment.
