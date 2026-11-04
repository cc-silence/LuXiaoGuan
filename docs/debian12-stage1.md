# Debian 12 adaptation: stage 1

This is an isolated development worktree, not a Linux release or an installer.
The Windows worktree remains at C:/Users/CC/Desktop/Work/RouterManager/RouterManager_python.
Do not build Windows packages from this Linux development directory.

## Agreed runtime

- Target OS: Debian 12 desktop.
- Use a private CPython 3.10 interpreter and the existing PySide6 6.3.0 pin.
- Do not replace /usr/bin/python3 or install application packages into system Python.
- The initial admission profile is an x86-64 candidate. User architecture confirmation
  and Debian desktop/session verification are still pending.
- Application dependency versions come from the existing requirements.txt.
- A future Linux bundle must include its interpreter. PyInstaller must run under
  the selected Linux interpreter; no Windows executable is reused as a Linux binary.
- The native terminal currently expects bin/router_terminal/winbox_terminal.dll.
  Its Linux library and matching Qt ABI need separate implementation/validation.

## Isolation and baseline

Branch: feature/debian12-runtime.
Git baseline: 37801cf662aeea936e0426683db3f0d920ef3ec4.
The current Windows source changes were copied into this worktree with SHA-256
verification, without committing or changing the Windows checkout.
The local source baseline inventory is build/debian12/source-baseline.json.
Generated Windows updater binaries, user configuration, router credentials,
release credentials and caches were not copied from the working directory.
The worktree itself contains the public files tracked by the baseline commit.

Do not periodically overwrite this worktree from Windows. Bring reviewed source
changes across deliberately, checking conflicts and baseline hashes first.
No Linux changes have been merged back into the Windows checkout.

## Completed in this stage

1. Move stable firewall ownership to an OS-neutral module.
2. Import the Windows registry/GPO backend only on Windows, preserving the original
   Windows callables and owner value.
3. Add scripts/linux_runtime_check.py. It checks OS, interpreter and architecture
   before inspecting dependencies, verifies exact application pins and core Python
   extension modules, and checks the Qt/PySide version match.
4. Run dependency imports in a subprocess with a 45-second timeout, so native
   library failures cannot crash the checker itself.
5. Keep runtime readiness separate from application validation in JSON reports.

This does not implement Linux firewall management, credential storage, desktop
shortcuts, configuration paths, terminal rendering or automatic updates.
Existing non-Windows fallback returns are not evidence of Linux feature support.

## Evidence and next admission step

On Windows, 31 focused tests passed:
python -m unittest tests.test_linux_runtime tests.test_startup_firewall -v

These include a simulated non-Windows import with registry/backend imports blocked.
They are not Debian execution evidence.
The checker correctly rejects this Windows host. No usable WSL Linux distribution,
Docker or Podman environment was found. No OS, driver or virtual machine was installed.

On a real Debian 12 build host, use the absolute path to the private Python 3.10:
PRIVATE_PYTHON -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/linux_runtime_check.py --report build/debian12/runtime-admission.json

PRIVATE_PYTHON above is a placeholder for a verified private interpreter, not a
command to install or replace system Python. Interpreter provisioning and a frozen
Linux build are still pending. A passed runtime report does not imply the GUI starts.

Next small stage: user configuration/work-data locations and their startup callers,
followed by the remaining startup services. Validate each before proceeding to
credentials, local file operations, native terminal and packaging/update work.

## Primary references

- PySide6 6.3.0 metadata, including Python <3.11 and Linux wheel architecture:
  https://pypi.org/project/PySide6/6.3.0/
- PyInstaller includes the active interpreter and requires platform-specific builds:
  https://pyinstaller.org/en/stable/operating-mode.html
- CPython private Unix builds; altinstall avoids replacing system python3:
  https://docs.python.org/3.10/using/unix.html

Stage 2 is recorded in [debian12-stage2.md](debian12-stage2.md).
