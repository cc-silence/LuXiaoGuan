# Debian 12 adaptation: stage 2

Scope: writable configuration, persistent preferences, capture work data and
startup error logs. All changes are in feature/debian12-runtime, in the isolated
Linux worktree. They have not been merged into the active Windows checkout.

## Directory policy

A Qt-independent platform service owns the directory policy:
modules/platform_services/application_paths.py.

Default Linux locations:
- Configuration: ~/.config/RouterManager
- Persistent preferences and error logs: ~/.local/state/RouterManager
- Recoverable capture work: ~/.local/share/RouterManager/capture-work

Absolute XDG_CONFIG_HOME, XDG_STATE_HOME and XDG_DATA_HOME overrides are honored.
Unset, empty or relative XDG values use the standard home-directory defaults.
ROUTERMANAGER_USER_CONFIG_DIR remains an explicit configuration override; on Linux
it must be absolute. Path resolution performs no filesystem writes.

New Linux directories are requested with mode 0700, including missing parents.
Existing directories are not chmodded. Filesystem errors propagate through the
existing configuration/work-directory APIs. Windows directory selection and its
portable configuration mirror retain their existing behavior.

Primary reference:
https://specifications.freedesktop.org/basedir/latest/

## Storage changes

- config_manager.CONFIG_DIR now uses the platform policy. This includes the
  split capture settings and UI preferences which previously used the install tree.
- On Linux, ConfigPair's primary and mirror point to the same user file.
  A shared read/write path performs one operation instead of two. Windows still
  writes distinct primary and mirror files and can recover from the surviving copy.
- Capture settings and persistent download-prompt preferences use the same
  directory-creation policy.
- WORK_DATA_DIR and CAPTURE_WORK_DIR use the Linux data location; the existing
  workspace ownership checks and temporary-directory fallback are unchanged.
- Startup error logs use the Linux state location without importing Qt/config.
  Redaction, bounded log retention and cleanup boundaries are unchanged.
- Known portable placeholders accept old Windows separators on Linux.
  Literal Linux paths containing backslashes are not globally rewritten.
  New Linux portable paths use forward slashes.

No data migration, deletion or background scan is performed. A previous
development copy's files are not automatically imported from the install tree.
There is no published Linux layout to migrate yet; any user-supplied historical
Linux data needs explicit identification before a migration is designed.
Bundled public update settings and application resources remain read-only resources;
the Linux updater, helper binaries and external download tools are still pending.

## Verification

33 focused tests passed on the Windows host:
python -m unittest tests.test_application_paths tests.test_linux_runtime tests.test_standalone tests.test_dependency_diagnostic_paths tests.test_owned_cache_paths -v

Evidence includes:
- XDG fallback, valid overrides and relative-value rejection.
- Windows directory compatibility and retained two-copy recovery.
- No duplicate disk IO for an identical primary/mirror.
- Save in one subprocess and reopen in a second, for settings, split capture/UI
  preferences and persistent download-prompt preferences.
- In the simulated Linux integration run, application writes and directory
  creation are rejected outside the temporary user root.
- Existing startup, appearance-setting persistence, capture fallback and cache
  boundary checks continue to pass.
- Private directory creation requests mode 0700 without altering existing folders.

These are simulation and Windows regression results, not Debian 12 execution
evidence. Native Unix permissions, a real read-only Linux installation, desktop
sessions and application startup still require a Debian 12 machine.
No interpreter bundle or Linux installation package has been produced.

## Next small stage

Review remaining Windows-specific startup lifecycle dependencies, particularly
administrator-session cleanup and first-launch desktop shortcuts. Keep the
Windows source directory isolated. Native terminal, secure credentials, local
file operations and Linux packaging/update delivery remain later stages.

Stage 3 is recorded in [debian12-stage3.md](debian12-stage3.md).
