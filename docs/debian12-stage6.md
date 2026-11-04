# Debian 12 adaptation: stage 6 - startup ownership and updater admission

## Startup import and single instance

The process service no longer constructs a Windows callback type at import time.
That type is created only with the Windows user32 backend.

Linux single-instance ownership uses a long-lived QLockFile lease in the private
desktop runtime directory. Only the lease holder removes a stale activation
socket and starts a user-restricted QLocalServer. A second process may notify the
owner; its shutdown cannot remove the owner's socket. Closing the owner releases
the listener before releasing the lease. Failed listener startup also releases
the lease. There is no timer or delayed retry used to repair an ownership race.

Runtime directories must be absolute directories owned by the current user;
the application subdirectory must be private. Linked or unsafe directories fail.
The activation backlog is bounded and accepted sockets are released.

## Updater admission: safety boundary, not Linux updater completion

The current channel, signed package format and native installation helper belong
to Windows x86-64. The Linux updater is NOT implemented by this stage.

One admission function now prevents an unsupported host/CPU from checking,
accepting a signed package, scanning an installation or starting a download with
that Windows updater. Linux startup does not read Windows testing-code credentials
or start Windows updater recovery/cache maintenance. Manual update operations
report the missing Linux implementation explicitly; they do not claim success.

Signatures, HTTPS checks, product validation and Windows release behavior remain.
No Linux feed, new signing key, installer format or architecture was invented.
Native Linux update installation/recovery, signed target/channel separation,
retention per target and packaging remain required before release.

## Evidence and remaining native work

- Isolated subprocess import smoke removes WinDLL, windll and WINFUNCTYPE while
  selecting Linux platform services and temporary user paths. Bootstrap imports.
- A manual offscreen smoke additionally ran startup, page switches and cleanup,
  returning 0. The Windows-only taskbar identity hook was mocked because the
  actual test interpreter still runs on Windows.
- Qt single-instance tests exercise lease exclusion, owner activation, secondary
  shutdown, owner handoff and listener failure cleanup.
- Updater tests reject even a correctly signed Windows manifest on Linux, avoid
  Windows credential reads/network startup, and reject unsupported CPUs before IO.

These are Windows-hosted contracts/simulations, not Debian certification.
Native Linux socket permissions, simultaneous processes, crash/stale recovery,
desktop activation and X11/Wayland window raising still need real validation.

## References

- https://doc.qt.io/QT-6/qlocalserver.html
- https://doc.qt.io/qt-6/qlockfile.html
