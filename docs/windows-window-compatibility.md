# Windows frameless window compatibility

The main window uses a translucent Qt surface with a custom frame and shared
edge/corner resize grips. Keep the client geometry under Qt control.

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
