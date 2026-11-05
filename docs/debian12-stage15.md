# Stage 15: native candidate build and independent retention

Checked: 2026-10-28. Debian 12 x86-64 desktop target only.
No Linux compilation, package launch, installer or cloud workflow was executed.
All active Windows source and its running test version were left unchanged.

## Compatibility assessment and references

This stage changes local desktop packaging and build-cache policy. It sends no
commands to OpenWrt, changes no router support inventory and does not alter UI.
Windows keeps its current EXE/ZIP build entry and release layout. Linux uses a
separate spec, candidate build entry and build/debian12/releases namespace.

The initial candidate is a PyInstaller onedir application with a private Python
runtime and an outer ZIP preserving Unix file metadata. This uses the established
cloud full-package ZIP contract; it does not claim a completed Debian installer.
Native compilation must run on Debian, not by renaming Windows output.

Primary references consulted:
- https://pyinstaller.org/en/stable/operating-mode.html
- https://pyinstaller.org/en/stable/spec-files.html
- https://docs.python.org/3.10/library/zipfile.html#zipinfo-objects

The build dependency remains pinned to the reviewed PyInstaller 6.20.0 candidate.
No dependencies were upgraded because the online documentation is newer.

## Build entry and admission

Read-only preview, including on Windows:

```text
python scripts/build_linux_release.py --plan
```

Native build, only after interpreter and terminal preparation on Debian:

```text
/absolute/path/to/verified/private/python3.10 scripts/build_linux_release.py --build
```

scripts/build_linux_release.py verifies actual Debian runtime admission, the
verified private-interpreter pointer, the PyInstaller pin and the exact native
terminal bytes/source fingerprints and smoke report. New resource files must be
version-controlled before a native build; otherwise it stops rather than omitting
an icon silently or collecting arbitrary local custom images.

RouterManager-linux.spec collects the application and Linux dependencies. It
analyzes the terminal shared library for Qt dependencies, then seeds that library
once at the application-root location expected by the native terminal loader.
No Windows updater is included. The existing Linux updater admission guard is
unchanged and remains closed.

Build phases have subprocess-group cleanup and timeouts. Frozen SSH and offscreen
GUI smokes run with temporary HOME/XDG/config paths, without desktop session
credentials. The build checks project bytecode plus the separately stored main
entrypoint, which includes the product version, against actual source. It checks
identity inputs again before assembling a candidate.

Outputs stay under build/debian12/releases with status
candidate-built-desktop-acceptance-pending. No publication descriptor is created,
no dist delivery is announced, and no app is installed. Match the common product
version before an actual release build; this preparation did not increment it.

## Inventory and archive validation

scripts/linux_release_layout.py selects version-controlled images/themes and
compatible router assets plus the explicitly validated terminal and its licenses.
It does not collect local configuration, remembered credentials, working data,
Windows executables or arbitrary untracked custom images. Assets are checked for
changes while copying. Only a source-controlled checkout should be used to build.

The installed-runtime baseline is schema 2 and records permissions, hashes,
compressed-object hashes and internal file links, compatible with the Linux update
catalog and old-runtime snapshot component. File content is streamed in 1 MB
blocks. Member count, directory count, depth and installation size are bounded.

ZIP assembly refuses to overwrite a delivery. It writes a temporary archive,
checks the resulting member bytes against the baseline, verifies Unix permissions,
link targets, completeness and CRC, and publishes only the verified archive.
A valid ZIP CRC alone does not prove the correct content; a separate test covers
wrong content with a valid CRC. No synthetic ELF fixture is ever executed.

## Storage and concurrency

scripts/build_cleanup.py owns independent Linux candidate cleanup. It retains one
verified Linux candidate and a recent failure record; unknown/running directories
are preserved. A corrupted retained archive stops cleanup before any deletion.
Failed builds drop their own reproducible dist/work directories and exactly named
incomplete candidate ZIP, while retaining logs and the prior usable candidate.

Cleanup validates resolved targets and ancestors before removal and never follows
internal PyInstaller library links. External links/reparse points cause a stop.
It never selects Windows build/releases or dist for Linux deletion.

The old-runtime snapshot now checks whether its nonblocking update lease was
actually acquired. Contention raises before creating a snapshot. A dedicated test
verifies that neither staged runtime nor installed files change on this path.

## Evidence and remaining gates

- build/debian12/stage15-release-preparation/summary.json: 14 modules, 127
  collected, 124 executed passes and three native filesystem skips under Windows.
- After the final failed-ZIP cleanup refinement,
  build/debian12/stage15-release-final/summary.json: six affected modules, 50
  collected, 49 executed passes and one native symlink-creation skip.
- Native symlink creation is unavailable on this Windows test host. Unix mode,
  no-follow and directory-sync semantics still require the actual Linux checks.
- The manual Debian workflow now selects 14 modules, including candidate layout,
  admission, retention and frozen-code validation. It was parsed locally only;
  no remote workflow was dispatched.

Still required: run the native runtime/terminal/build chain; exercise real desktop
and dependencies; finish installation, updater worker launch, atomic replacement,
rollback and crash reconciliation; wire Linux publisher preparation after those
release gates pass. Current source alignment remains the reviewed committed
Windows f38c3fcf baseline; pending Windows development is reported separately.
