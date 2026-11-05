# RouterManager native terminal

Recovered from the exact 502 source fingerprints in the existing Windows terminal
build record, bin/router_terminal/build-manifest.json. Upstream revisions and source
archive fingerprints are in upstream.json; vendored licenses remain with sources.
The Qt renderer and external SSH transport are shared by Windows and Linux.

Local portability changes: Qt symbol export macro, compiler-specific qmake C flags,
and a CMake entry requiring Qt 6.3.2 exactly. Linux output is
libroutermanager_terminal.so; the deployed Windows DLL is unchanged.

Build on an admitted Debian 12 x86-64 / private Python 3.10.22 candidate host:

    PRIVATE_PYTHON -m scripts.build_linux_terminal --qt-prefix /path/to/Qt-6.3.2

This does not install system prerequisites. A matching development SDK is required;
the Debian 12 system Qt version differs. A newly built library must pass the real
terminal smoke before replacing any prior Linux library. Native build and desktop
acceptance are still pending. Runtime maintenance must be reviewed before release;
Python 3.10 is now end of life. See docs/debian12-stages7-9.md.
