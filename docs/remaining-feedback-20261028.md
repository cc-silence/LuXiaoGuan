# Remaining feedback fixes: 2026-10-28

## Completed in source

### Windows IME uppercase input

The application pinned PySide6/Qt 6.3.0. Official Qt issue QTBUG-98003 describes capital letters disappearing with input methods and Shift; its fix versions include 6.3.1. The official change removes disabling/re-enabling Windows IME from `QWindowsInputContext::showInputPanel()`. Source inspection confirms the sequence exists in v6.3.0 and is absent in v6.3.1/v6.3.2.

The application now pins PySide6 6.3.2, keeping the same Qt minor series. No text rewriting, timers, input-method disabling, or restrictions on Chinese input were added. The release builder checks that both PySide and Qt match the dependency pin before staging a package, preventing the old runtime from silently entering a new EXE.

- Official issue: https://bugreports.qt.io/browse/QTBUG-98003
- Official fix: https://github.com/qt/qtbase/commit/067b53864112c084587fa9a507eb4bde3d50a6e1
- Fixed source: https://raw.githubusercontent.com/qt/qtbase/v6.3.2/src/plugins/platforms/windows/qwindowsinputcontext.cpp

Validation uses a project-local environment at `build/qt632-env`, with system packages inherited and PySide6/shiboken6 6.3.2 installed locally. The global Python environment and existing releases were not replaced. Build/run with that environment or install requirements-ci.txt into another controlled build environment.

Synthetic input tests cover uppercase key text, input-method commits, Chinese text, deletion, and password visibility. They verify application behavior but cannot substitute for the user's native Win11 Microsoft Pinyin sequence. The upstream fix is directly applicable to the shipped runtime; this is not a claim that the historical user event was reproduced locally.

Python compatibility is unchanged: PySide6 6.3.x requires Python below 3.11. This Windows branch does not establish compatibility with Debian 12's default Python. The separate Debian implementation and actual Linux GUI acceptance remain outstanding.

### Relocation cleanup

The native single apply with a 90-second rollback remains unchanged. A separate pinned SSH channel handles MAC-change interruptions; only read checks and rollback recovery reconnect. The configuration-changing stage is submitted once. Command budgets are recalculated after connecting. The interpreter discovered during preview is reused instead of probing repeatedly.

Recovery reply loss is distinguished from failed recovery: original configuration revisions can establish restoration even when the rollback acknowledgement/session-destroy reply was lost. A lost confirmation reply that leaves the new configuration active remains uncertain and never reports restoration. Recovery error classification stops at the prior transaction error, preventing unrelated implicit exception context from hiding a subsequent transport failure.

After successful verification, the independent authenticated transport may be adopted through the existing SSH generation/client/fingerprint guards. Active or newer logins and explicit disconnects are never overwritten. LAN and relocation share connection-state publication. Transport credentials are cleared on close; structured logs keep phases and counts, not passwords, commands or settings.

The old feedback archive did not include the detailed relocation task. These proven code defects and new tests do not establish the exact historical cause of that particular warning.

## Validation

165 targeted tests passed using Python 3.10.11 and Qt/PySide6 6.3.2. The suite includes relocation Lua/JSON protocols, connection loss, cancellation, identity refusal, bounded deadlines, connection adoption, rollback/confirmation reply loss, LAN force apply, factory reset, logs, shared input widgets, theme scope and UI lifecycle. Existing offline-button coverage was updated to the explicitly requested click-to-login prompt contract.

Source hashes, test count and limitations: `build/review/remaining-feedback-20261028/summary.json`. Dark/light UI drawing at 100%/150% was inspected after the runtime update. Eleven changed Python modules/tests passed compilation and UTF-8 checks. Whitespace checks passed.

## Still requiring real acceptance

- Win11 Microsoft Pinyin: Caps Lock then first uppercase letter; Shift+letter then release Shift; password and signal name; Chinese input and password eye toggle.
- Authorized router relocation: successful wired-LAN cleanup, temporary SSH interruption, original-device reconnection, cancellation and native automatic rollback when confirmation is unavailable.
- Debian 12 GUI/runtime and dependency acceptance.

No real router or network adapter was modified. No application build, version bump, commit, push or publication was performed. Existing EXEs do not contain these source changes.
