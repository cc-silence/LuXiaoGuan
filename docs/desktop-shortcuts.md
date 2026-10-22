# Desktop shortcut behavior

The global settings page exposes an immediate desktop shortcut action. It does
not depend on saving or accepting the settings page. Creation and inspection
share one runtime-owned controller, so closing settings does not destroy a task.

The main window is shown first. After the startup firewall gate succeeds, the
shortcut presenter handles first-launch inspection and confirmation. Its completion
signal queues the existing automatic update entry point. Smoke tests never enter
the firewall/first-launch sequence.

The per-user record is .desktop-shortcut-prompted under the existing RouterManager
user configuration directory. It is independent of installation directories and is
not reset by replacing application files. Accepting, declining or discovering an
already valid shortcut records the decision. Inspection failures do not suppress
future first-launch retries. Manual creation remains available regardless.

The platform service creates the current user's desktop shortcut with the Chinese
display name from modules/release_identity.py. It uses the actual registered
desktop, including relocated/OneDrive desktops. An unavailable registered desktop
is reported instead of silently writing to a different folder.

Frozen shortcuts launch the current application executable with its embedded icon.
Source shortcuts launch pythonw.exe with an explicitly quoted launch.pyw argument
and the router ICO resource. Both set the installation working directory.

A valid shortcut is retained. A recognized application shortcut pointing to an old
location is repaired. A same-name shortcut to another program requires confirmation.
The shell process checks the inspected file fingerprint again before replacing it.

Shell paths and arguments are encoded as JSON data. One hidden QProcess has a
10-second operation deadline and bounded output buffers. Exit kills that process,
waits for its finished signal, and removes its uniquely named temporary shortcut.
No polling or delayed window-display workaround is used.

Tests use temporary desktops and per-user state directories; they never write a
shortcut on the real desktop. Source Windows shell integration, lifecycle tests,
settings theme/scale captures, and release asset seeding are covered. No release
archive or public update is produced by these tests.
