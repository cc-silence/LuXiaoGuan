# Debian 12 adaptation: stage 5 - local filesystem operations

Changes remain in the isolated feature/debian12-runtime worktree.

## Implemented

- The shared recycle-bin entry point lazily selects Windows COM or the pinned
  Qt 6.3 QFile instance method on Linux. Failure never becomes permanent deletion.
  Existing identity, confirmed-parent, link rejection and cancellation checks stay.
- Local directory names follow the host rules. Linux names are no longer rejected
  merely for Windows device names, colons, backslashes or trailing dots/spaces.
  A name must still be a single component, excluding slash, NUL, dot and dot-dot.
- Directory downloads apply Windows name restrictions and case folding only for
  Windows destinations. Existing remote-path and special-file restrictions remain.
- Linux publication uses libc renameat2 with RENAME_NOREPLACE, for files and
  directories. An existing target is rejected by the kernel rather than by a
  check followed by an overwriting POSIX rename.
- The shared primitive covers file-manager moves, new single-file downloads,
  directory downloads and offline dependency exports. Explicitly approved
  replacement paths keep their existing replacement semantics.
- Missing kernel/filesystem support returns an error. There is no copy/delete or
  overwriting fallback. Windows keeps its existing native rename behavior.

## Verification boundary

Synthetic tests cover native-name selection, atomic no-replace flags and errors,
unsupported filesystems, cancellation, recycle-bin failure without source removal,
directory creation, move collisions and shared single-file publication. Existing
download and export regressions also run.

A separate native Linux test checks real file and directory collisions. It is
explicitly skipped on Windows and allowed only as that specific Windows CI skip.
No real user file was moved to the recycle bin during these tests.

Still required on Debian 12: actual trash creation/restore on the chosen desktop,
renameat2 behavior on supported volumes, permissions, filename case sensitivity,
cancellation/partial results, directory transfer and packaged runtime execution.

## References

- https://doc.qt.io/qtforpython-6/PySide6/QtCore/QFile.html
- https://doc.qt.io/qt-6/qfile.html
- https://www.man7.org/linux/man-pages/man2/renameat2.2%40%40man-pages.html

The installed PySide6 6.3.0 binding was inspected directly: QFile.moveToTrash(self)
returns bool. The newer supportsMoveToTrash API is deliberately not used.
