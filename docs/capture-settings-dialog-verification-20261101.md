# Capture settings dialog verification (2026-11-01)

Scope: move the capture settings entry out of the navigation tabs and into the capture action sidebar. Reuse the existing settings editor and immediate-save signals inside one lazily created FramelessDialog. The editor remains available for configuration collection while closed; page disposal retains the existing dictionary-preview cancellation and snapshot disconnect. No router command, capture parameter semantics, backend or platform implementation changed, so OpenWrt operation compatibility research is exempt for this UI-only change.

Verified on Windows with Qt offscreen rendering:
- Seven navigation pages retain their order; navigation theme/scale checks cover 100, 125, 150 and 175 percent.
- Following the owner follow-up, the sidebar button uses the shared text button, has no icon, and centers its text. The capture navigation title is now regular capture.
- Button opens one window-modal dialog; closing and reopening reuses it and preserves edits.
- Changed values reach the isolated configuration file through the existing save chain.
- Dark/light rendered dialog checks at 100 and 150 percent cover button width and checkbox background continuity.
- Existing settings content and dependency-card regressions pass.
- Missing-password-dictionary guidance now opens this dialog instead of selecting the removed tab.
- Syntax compilation, UTF-8/mojibake scan and git diff whitespace checks pass.

Six distinct targeted tests passed: the seven-page integration case, the capture settings dialog case, the rounds-combo rendering case, and three manual-PMKID-removal cases. Screenshots are under build/capture-settings-dialog-review.

Debian 12 x86-64 desktop acceptance remains pending. The changed UI uses shared Qt components without OS branches. On Debian, start the application, open Capture, verify icon/text and settings dialog under both themes and application scaling, edit a setting, close/reopen, then exit and confirm background dictionary preview cleanup. Offscreen Windows rendering is not Debian desktop or X11/Wayland verification.


## Follow-up: wheel routing and text-only settings button

The shared ParameterWheelGuard now forwards a mapped copy of child-widget wheel events directly to the nearest QAbstractScrollArea viewport. This covers disabled buttons as well as controls whose wheel guard previously consumed the event without a reliable parent handoff. The copy retains angle/pixel deltas, input modifiers, scroll phase, inversion and source. Native viewports, scrollbars and open popup lists retain native handling. The guard uses the existing weak widget-style registration, is installed once, keeps no widget cache, and adds no timer or polling. This is local Qt input routing and does not change router operations.

Qt reference consulted on 2026-11-01: https://doc.qt.io/qt-6/qwheelevent.html (unhandled wheel events must be ignored to propagate to parents). Runtime checks used the project virtual environment, PySide6/Qt 6.3.2 on Windows.

Six wheel-routing cases pass, covering QScrollArea, BounceScrollArea, ActionScrollArea, enabled/disabled buttons and labels, unchanged parameter values, dialog controls, nested scroll ownership, native text viewports, popup-list isolation and late-created widgets. Two actual application tests pass for dialog saving/rendering and the disabled capture-dependency install button. The real startup style-registration regression also passes. One combined test process did not finish and was stopped; these focused checks were then executed in isolated processes. This does not claim a full-suite run.

The previously opened source application keeps its loaded code until restarted. Debian 12 desktop input delivery remains pending; repeat the hover-and-wheel checks on buttons, disabled controls, parameter fields, native lists and popup lists under the actual X11/Wayland desktop session.
