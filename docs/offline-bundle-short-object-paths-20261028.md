# Short offline object and ZIP cache paths

2026-10-28. Common and capture bundles share the same local layout helpers.

Prepared objects use `versions/YYYYMMDD-NN/objects/000001` rather than 64-byte
hex names. The six-digit numbering is computed from sorted full SHA-256 identities
in the authenticated catalog, not truncated hashes. An `object-layout.json`
marker distinguishes numbered files from unrelated numeric files in old layouts.
The signed catalog, full SHA-256 values and published ZIP bytes are unchanged.
An old 189-character relative object example is now 78 characters (111 fewer).

ZIP downloads use `archives/YYYYMMDD-NN/bundle.zip` and `bundle.zip.part`.
`resume.json` retains the complete signed release. Old full-hash directories and
original ZIP names remain discoverable. Download preparation atomically renames
existing ZIP/partial files into the short location on the same volume; a retry
reuses the same directory and preserved bytes if either move was interrupted.
No duplicate full archive is copied. Activation still removes redundant ZIPs;
cleanup recognizes old and new layouts and preserves unknown files and links.

The shared repository resolves both layouts for installation and extraction.
Re-preparing old verified objects moves them to their short names. Interrupted
migration remains readable through mixed short/legacy resolution and is retryable.
Read-only legacy loading never writes or migrates files. Short metadata commits
are atomic; a failed commit leaves the old layout usable. Opening a page does
not start a synchronous filesystem scan or migrate user resources.

Old application versions cannot read newly numbered local objects; use the new
application with newly prepared resources. Published cloud ZIPs retain the
compatible old wire format; the new application saves their extracted contents
under short names. This does not require redownloading cloud offline packages.
Already existing prepared directories remain readable and get converted when
prepared again; this task does not directly edit an installed external copy.

## Compatibility evidence

- [Microsoft path limits](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation)
  documents MAX_PATH and the separate requirements of shell/file APIs. Shortening
  names reduces actual path length without assuming every copy tool supports long
  paths; arbitrarily deep installation locations are still not guaranteed.
- [Python 3.10 pathlib rename](https://docs.python.org/3.10/library/pathlib.html#pathlib.Path.rename)
  supplies the common Windows/Linux filesystem interface. Destinations are checked
  before migration and all changes remain within the existing resource lease.

No OpenWrt commands, firmware profiles, package contents, real ipk/apk filenames,
package-manager behavior, router model selection, or signatures change. Coverage
includes common/capture and opkg/apk resources, actual legacy layouts, integrity
failures, user-name collisions, interrupted migration, atomic-commit failure,
old partial download resume, ZIP cleanup and normal version retirement.

Windows/Python 3.10/Qt 6.3.2 is the local regression environment. Debian 12 uses
identical pathlib and streaming logic but has not been tested on an actual Debian
runtime here. No real router or external installed resource directory is modified.
Client packaging and publication remain pending.

## Verification result

116 targeted regression tests passed in the combined run, plus one real Windows
deep-directory test. That additional test completes download/preparation under
260 characters where the previous object path would exceed 260, and rejects any
attempt to resolve the absent legacy object path while reading short objects.
Source checks, explicit compilation of modified/new Python files, UTF-8 checks
and git diff whitespace checks passed. Logs: `build/short-path-complete-tests.log`,
`build/short-path-audit-tests.log`, and `build/short-path-extra-tests.log`.

For `C:/software/LuXiaoGuan/offline_packages/openwrt_capture`, an example old ZIP
path is 186 characters versus 87 for `archives/20261028-01/bundle.zip`.
This is a calculated example; the user's named external directory was not found
in this environment, and no files under that installed location were modified.
