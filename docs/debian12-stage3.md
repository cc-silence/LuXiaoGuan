# Debian 12 adaptation: stage 3

Scope: startup/exit platform boundaries and desktop launcher creation.
All adaptation remains in the isolated feature/debian12-runtime worktree.
No changes have been merged into the Windows checkout.

## Implemented

- The startup firewall controller records applicability. On Linux its stage is
  not_applicable: no Windows GPO audit, administrator process or audit thread runs.
  This permits a network attempt; it does NOT certify or change Linux firewall rules.
- The presenter does not create the Windows audit dialog on an inapplicable host.
  Windows auditing, cancellation/error gating and the original modal flow remain.
- Runtime startup completion uses an explicit queued Qt connection, so an immediate
  inapplicability result cannot synchronously reenter first-launch prompts.
- A platform cleanup hook imports/closes Windows administrator sessions only on
  Windows. Linux shutdown does not import the named-pipe or WinAPI implementation.
- The shared shortcut controller selects a Python worker for .desktop launchers,
  retaining its existing timeout, bounded output, cancellation and child cleanup.
- Linux launchers use the selected private source interpreter or the future frozen
  application executable. No system Python lookup, shell, PowerShell or pythonw.exe
  is introduced into the Linux launcher.
- The Desktop location comes from QStandardPaths. An unavailable configured desktop
  is reported rather than replaced by a newly invented directory.
- The worker handles small UTF-8 .desktop files, executable permissions, quoting,
  owner identity, content fingerprints and same-directory staging. It checks changes
  before replacing existing files. New launcher publication uses a no-overwrite link.
- Existing unrelated launchers still require the existing confirmation. Unexpected
  staging files and oversized/nonregular launchers are rejected.
- The helper dispatch occurs before GUI bootstrap and shares the source/frozen
  application entry point. First-launch prompt markers use private directory creation.

Windows shortcut code and its public contract are retained. Native Windows helper
source and user-desktop lookup are now imported lazily only for Windows operations.
No additional timer or polling workaround was added.

## Verification

First run: 66 tests passed:
python -m unittest tests.test_linux_startup tests.test_linux_runtime tests.test_desktop_shortcut tests.test_shortcut_presenter tests.test_shortcut_settings tests.test_startup_firewall tests.test_standalone -v

After adding explicit queued startup completion and its regression, all 5 settings
tests passed, including the new test:
python -m unittest tests.test_shortcut_settings -v

This represents 67 distinct tests across the two runs. Windows redirected-desktop
tests performed actual native shortcut inspection, creation and repair in temporary
directories. Existing timeout/cancel tests verified the child is reaped, and the
settings suite exercised light/dark themes and scaling.

Linux startup tests blocked Windows session imports, checked no Windows audit thread
or dialog was started, verified worker dispatch avoids GUI bootstrap, and tested
launcher creation/repair, foreign-file confirmation and concurrent file appearance.
Because the host is Windows, Unix execution-bit reporting was explicitly modeled
in Linux launcher creation tests. Unix permission semantics are NOT verified here.

## Remaining boundaries

No Debian 12 execution environment is available in this session. Real desktop
launching, native permission bits, localized Desktop lookup, X11/Wayland and any
desktop-specific trust prompt still require real Debian verification. A desktop
environment may require the user to trust the launcher; this code does not disable
or bypass that desktop policy.

This stage does not complete the Linux application:
- The terminal still expects a Windows DLL and needs a Linux library with matching Qt ABI.
- Frozen startup recovery and the updater still depend on the Windows update route.
  Linux packaging, platform-specific manifests and update installation are pending.
- Secure credentials and local file/recycle-bin operations remain to be adapted.
- No Linux installer or interpreter bundle has been built.

Next small stage: add the shared secure-credential interface and the Linux backend,
retaining Windows credential behavior without storing passwords in plain config.

## Primary references

- Freedesktop launcher command arguments and escaping:
  https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html
- Freedesktop launcher fields:
  https://specifications.freedesktop.org/desktop-entry/latest/recognized-keys.html
- Qt desktop directory lookup:
  https://doc.qt.io/qtforpython-6/PySide6/QtCore/QStandardPaths.html

Stage 4A is recorded in [debian12-stage4.md](debian12-stage4.md).
