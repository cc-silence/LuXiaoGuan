# Windows frameless window compatibility

The main window now uses an opaque Qt native surface with a themed rounded
window mask and the existing custom edge/corner resize grips. Keep the client
geometry under Qt control. Shared dialogs retain their own translucent shells.

Do not add WS_THICKFRAME through SetWindowLongW after Qt creates this window.
On the verified Windows environment this adds 7 native pixels on every side:
Qt renders 1280x720 while Windows allocates an outer rectangle of 1294x734.
The process remains responsive, the taskbar button exists, and ordinary dialogs
work, but the main layered surface is never presented.

The 2026-10-21 A/B reproduction is in build/window-ab.json and its paired screen
captures. With the native frame adaptation enabled, the actual screen showed the
window underneath; with Qt geometry retained, the application rendered normally.
The fix preserves system-menu/minimize/maximize flags and existing Qt resize grips.
No delayed repaint, periodic restore, opacity workaround, or layout change is used.

Verify changes with the actual Windows display backend:

    $env:QT_QPA_PLATFORM = 'windows'
    python -m unittest tests.test_native_window_visibility -v

This test checks native frame/client agreement and that screen hit-testing finds
the main window, across light/dark themes, minimize/restore, maximize/restore and
hide/show. Offscreen smoke tests alone cannot detect this presentation failure.
Also execute the real Windows presentation probe against the packaged executable
before delivering a release containing window-shell changes.


## 2026-11-02: opaque main-window surface

The owner reports a light-theme desktop-through-background defect on Windows 11
with 1.0.28_alpha. Windows 10 did not show the reported defect. Existing fixes
supplied the full shell stylesheet and painted the frame from the top-level
paint event, but retained the translucent native-window contract. The old native
presentation test only hit-tested the center; that could miss transparent chrome.

RouterWindow now opts out of WA_TranslucentBackground before native creation.
WindowSurface uses the resolved frame palette as an opaque native background,
and derives the outer rounded region from the existing scaled frame QSS radius.
The shape is updated only on geometry/show/state changes or appearance updates;
no timer, repaint loop, screenshot backing cache, native thick frame, or hardcoded
Win11-only theme branch was added. Only the region bounding the rounded corners
is clipped; the interior does not depend on child painting for opacity.

The prior alpha-at-corner assertion now checks the actual native mask exclusion;
QWidget.grab() can include pixels outside an opaque window's native mask, so its
corner alpha is not a native shape test. Interior opacity is still checked while
the entire content frame is hidden at 100/125/150/175 percent and repeated dark/
light transitions. The real Windows backend checks absence of WS_EX_LAYERED,
window/client geometry, hit-testing, minimize/maximize/restore and hide/show,
plus screen-captured interior pixels against alternating magenta/green windows
behind the application. Actual screen captures are under build/windows-surface-review.

Local environment: Windows 10 Enterprise LTSC 2021 build 19044, project Qt/PySide6
6.3.2. This is a source change, not a packaged release. Windows 11 and Debian 12
actual desktop acceptance remain pending. On Windows 11, repeat light-theme cold
start, dark/light changes, resizing, minimize/restore and maximize/restore, and
inspect title bar, navigation margins and footer against a contrasting desktop.
Do not infer Win11 success from the Win10 native tests or offscreen rendering.

Qt reference consulted: https://doc.qt.io/qt-6/qwidget.html (translucent windows,
autoFillBackground and window masks). This is a local desktop presentation change;
router commands, package operations and device compatibility behavior are unchanged.
