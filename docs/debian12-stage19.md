# Stage 19: Linux desktop update and release integration

## Implemented source paths

- The Linux desktop checks its Linux channel, asynchronously reads testing codes
  through the shared encrypted credential service, and plans signed schema-2 updates.
- Streaming downloads verify compressed and raw objects, preserve Unix modes and
  internal links, recover declared partial downloads, support cancellation and
  bounded route retries, and prepare the independent verified old runtime.
- The desktop waits for a pipe readiness signal from the independent updater.
  Pipe and process notifications replace polling; a bounded startup deadline
  terminates only the updater child that failed to become ready. Main-window
  shutdown is blocked if another task starts during handoff.
- The independent progress window uses the existing common dialog, target theme
  and scale from construction, background installation, and explicit failure text.
  Active transactions cannot be cancelled by closing the window. Recovery uses
  the stable launcher and existing durable transaction journal.
- Completed stages are pruned under both worker and UI leases. Only verified
  managed files are removed; unknown or changed files are retained. Linux updater
  evidence uses the same XDG state log directory as the application and the
  existing bounded, credential-free export format.
- A normal-user installer copies a new verified installation, creates an optional
  user menu shortcut, and refuses overwrite. Uninstall refuses changed critical
  files, preserves customized resources and XDG user data, and blocks pending
  updates. Distribution remains a Unix-metadata ZIP, not a .deb package.
- Linux package preparation and publication now accept only validated Linux
  descriptors and inventory. Windows and Linux stable/alpha slots remain distinct.
  Existing retention and quota-only alpha delete-before-upload behavior is kept.
  This stage performed no cloud upload or deletion.

## Admission and native commands

The source checkout has no native admission record and Linux automatic updates
remain unavailable here. A Debian candidate builder must pass all nine native
file/process/transaction/desktop modules with zero skips before recording native
update admission inside the package. Publication also requires separate acceptance
metadata. A candidate archive alone is not a publishable release.

On a normal-user Debian 12 x86-64 desktop, prepare the approved private interpreter
with `python3 -m scripts.prepare_linux_python --source /absolute/Python-3.10.22.tar.xz`.
Use the verified interpreter recorded in `build/debian12/python-runtime/current.json`
for the pinned dependencies, exact Qt 6.3.2 terminal SDK/build checks, and then:

```
PRIVATE_PYTHON scripts/build_linux_release.py --build
PRIVATE_PYTHON scripts/accept_linux_release.py --candidate-report /absolute/candidate-report.json
```

`PRIVATE_PYTHON` above means that recorded executable, not Debian's system Python.
See the existing runtime and terminal preparation documents for SDK provisioning.
Acceptance requires an actual graphical session, reruns native regressions, and
checks frozen startup, normal-user install, uninstall and user-data preservation.
It emits release metadata without publishing. Use the existing publication flow
only after separately reviewing that evidence.

## Current evidence and limits

- Host: Windows, Python 3.10.11 and isolated PySide6/Qt 6.3.2 audit environment.
  This is not the admitted private Debian interpreter (3.10.22 or newer).
- Final targeted audit: 38 modules, 332 collected, 310 executed passes and
  22 expected native Linux skips on Windows. No failed modules.
- Final targeted regression results: `build/debian12/stage19-final-verified/summary.json`.
  Imported fixture cases count toward totals. Native Linux skips must be disclosed.
- Actual common-window construction was rendered in light/dark at 100/150 percent.
  Screenshots: `build/debian12/stage19-visual/`. The offscreen Qt host required an
  explicitly loaded installed Chinese font; no font was embedded in a release.
- Source policy, Python compilation, UTF-8 replacement-character scan, workflow
  YAML/two embedded-Python blocks, 27-module selection and diff checks passed.
  These are local checks. The manual Debian workflow was not dispatched.
- Committed Windows baseline: 53af14541ca718188fc495e18738c8bdc9158af6,
  version 1.0.22_alpha. This commit has the same tree as the previous recorded
  aed7da0a baseline. The Windows working checkout has additional uncommitted source
  changes; these are listed in `build/debian12/stage19-source-alignment.json` and
  are not implicitly merged or certified. The Windows source was not edited here.

## Still requires Debian

No native Linux ELF terminal, frozen application, installer archive or certified
Linux release was produced. Real Debian build and desktop startup, two-real-version
upgrade/rollback/interruption recovery, keyring locked/unlocked persistence, trash,
shortcuts, instances, Chinese fonts, tray, scaling, X11/Wayland and authorized-router
workflows remain unverified. Model tests and offscreen screenshots do not replace
these gates. Python runtime migration remains deferred by the user's decision.

## Public component API

`TaskProgressDialog(..., theme_name=None)` preserves existing callers. A supplied
name constructs the common shell with that theme immediately; `None` reads the
current configured theme. `apply_common_dialog_theme` accepts the same optional
keyword. This avoids constructing a new updater with one theme and immediately
switching it. Styles, colors and geometry remain owned by existing common QSS.

## Primary references reviewed

- https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThread.html
- https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html
- https://requests.readthedocs.io/en/latest/user/advanced/#body-content-workflow
