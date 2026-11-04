# Stage 17: independent Linux worker and stable recovery entry

Checked: 2026-10-28. Target: Debian 12 desktop, Intel/AMD x86-64.
This work changes only the Linux development worktree. No active Windows source,
cloud package, router, application version or desktop shortcut was changed here.
No Debian system, WSL environment or remote CI workflow was installed/dispatched.

## Process identity and resource boundaries

modules/platform_services/linux_update_process.py records the parent PID, kernel
start ticks, boot ID and executable device/inode. Waiting uses a Linux pidfd and
one kernel wait rather than polling or terminating the parent. A previous boot,
a disappeared parent or a reused PID does not cause a wait on an unrelated task.
Handle errors and timeouts stop before installation.

modules/platform_services/linux_update_child.py supplies clean child environments
and a bounded native startup probe. It resets PyInstaller bundle state, retains
normal user/session/config paths for restart, and uses isolated temporary user
paths plus offscreen Qt for validation. Probe output is capped at 256 KiB with a
16 KiB tail; only existing sanitized startup-evidence categories are retained.
A timed-out probe cleans its own process group and reaps its own child. It never
kills the recorded parent. Default SIGCHLD reaping is required so the validation
child PID remains reserved until its group cleanup finishes.

References checked:
- https://man7.org/linux/man-pages/man2/pidfd_open.2.html
- https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html
- https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#using-sys-executable-to-spawn-subprocesses-that-outlive-the-application-process-implementing-application-restart

## Job preparation and explicit launch commit

modules/app_update_services/linux_update_jobs.py verifies the real Ed25519 release
signature with the built-in product key and explicit linux-x64 target, then checks
the catalog digest, canonical encoding and stage identity. Directory ownership,
permissions, private job mode, installation inode and parent ticket are checked.
The job does not contain passwords, private keys or alpha testing credentials.

Background preparation performs installation preflight and prepares the stable
old runtime. Legitimate changes to official theme/image/script contents can enter
an explicitly local snapshot inventory with correct content/compression hashes.
Changed critical program/runtime files are rejected. Unsupported resource modes
or external/dangling links are rejected, not silently rewritten or copied outside
the managed tree. The snapshot remains private and is never a release catalog.

Preparation has cancellation and a total 300-second ceiling. It flushes verified
manifest/catalog metadata and the private job before an update can be armed.
Preparing a download does not publish the startup recovery pointer, so a merely
prepared update does not intercept normal application launches.

modules/platform_services/linux_update_install.py exposes separate APIs:
- prepare_helper: background work after the download lease has been released;
- launch_helper: a short commit checking the prepared job/current parent and stable
  entry, publishing the recovery pointer and spawning the isolated worker.

The launcher does no full-runtime scan or copy on the UI launch path. The worker
reverifies signed input and the full snapshot before mutation. Do not call heavy
preparation inside the shared download lease: snapshot preparation owns that same
nonblocking lease. The desktop download/controller still needs this integration.

## Worker and restart

modules/app_update_services/linux_update_worker.py runs from the verified old
frozen runtime, under its own nonblocking worker lease. It waits for the recorded
parent, invokes the Linux transaction, runs a bounded new-program smoke check,
and reports private progress/result records. It reuses the core rollback rules.

A prepared-only interrupted job is cancelled on recovery rather than unexpectedly
installed at startup. An interrupted transaction is reconciled through its journal.
A completed commit finishes cleanup without reverting the validated new version.
Critical runtime files are checked before restarting after recovery. Unknown edits
or unrecoverable sidecars leave the pointer/evidence intact and do not restart a
partially recovered installation. A failed restart is recorded separately from a
successfully applied installation and does not undo a validated commit.

## Stable entry and package contract

modules/platform_services/linux_recovery_launcher.py defines the fixed protocol-1
LuXiaoGuan-start shell entry. It depends on Debian's shell/core utilities, not the
system Python. A strict 64-character revision pointer selects the cached old
runtime. Unknown/malformed pointers, linked control locations or an active update
without a pointer stop startup. If the installed main executable is missing, the
script can still execute the cached runtime's recovery flag.

main.py dispatches worker/recovery flags before desktop imports. Normal frozen
Linux startup redirects through the stable entry when recovery is pending; startup
smokes bypass this redirect so validation cannot recursively launch recovery.
Windows startup keeps its existing path. Frozen Linux desktop shortcuts select
the stable launcher when present; the existing shortcut ownership/style rules stay
unchanged. This source change did not write any actual desktop shortcut.

The Linux complete ZIP must contain the exact executable stable launcher. Package
assembly, CRC/content verification and publisher artifact preparation validate its
bytes and mode. The launcher is deliberately excluded from the mutable baseline
and incremental update catalog, so normal app updates cannot replace their own
recovery entry. Keep the deployed protocol compatible when evolving this code.

## Evidence

build/debian12/stage17-worker-launcher-final/summary.json:
- 15 modules, 144 collected tests, 133 executed passes and 11 explicit native skips.
- 15 worker cases use real Ed25519 input and real temporary file transactions,
  with Windows metadata/ownership/process models where native semantics differ.
  No synthetic ELF fixture is executed. Linux CI uses actual directory-fd storage;
  frozen context, actual GUI parent/probe execution remain separately mocked here.
- Recovery is tested after parking the main executable, leaving it missing, then
  restoring the old installation through the worker while retaining user config.
- 11 dispatcher/launcher cases include shell execution under explicit Git Bash,
  Chinese/space paths, argument preservation, environment reset, malformed pointer
  refusal, desktop target selection, Windows startup and smoke recursion guards.
  Git Bash results are shell/protocol evidence, not Debian desktop acceptance.
- Three new native pidfd/process-group cases were skipped on Windows. The earlier
  six directory-fd cases and two native rename/symlink cases were also skipped.
- Existing Windows update, shortcut, settings, frozen-source and startup tests pass.

The manual Debian workflow now selects 19 modules. Its native process and file
cases must run rather than skip on Linux. It remains manual, read-only and has not
been dispatched or used to claim a Linux build.

## Remaining release gates

The live Linux updater admission remains closed. Still required: desktop preview/
download/controller selection, main shutdown/progress/error coordination, native
runtime-cache cleanup respecting the worker lease, Linux publisher application
preparation and a complete user installation/uninstall flow preserving XDG data.

Actual Debian must compile private Python/terminal, build and execute the frozen
candidate, and verify pidfd, reaping, desktop restart, permissions, disk-full,
interruption recovery, keyring and real application/device behavior. Partial
sidecars or unexpected concurrent edits can still require manual reconciliation;
this stage does not promise automatic recovery from every damaged local state.

## Concurrent Windows baseline change detected at final check

Windows advanced during this work to committed revision
6237a975a3a9773ff54cb7353b5795eb73fc528d, version 1.0.22_alpha. Its requirements
changed PySide6 from 6.3.0 to 6.3.2 and added an exact Qt runtime packaging check.
The alignment guard correctly reports source-sync-required. The Linux reviewed
baseline remains f38c3fcf / 1.0.21_alpha; none of the new Windows working edits or
its Windows updater binaries were copied here.

The 81 newly committed paths need a separate reviewed synchronization, including
capture/settings behavior and the Qt patch-level profile. Before a Linux build,
review the 6.3.2 Python wheels/Qt SDK combination, align runtime/terminal checks,
and run the affected regressions. Stage 17 evidence does not certify this newer
Windows baseline and must not be used to publish a matching Linux release.
See build/debian12/stage17-worker-launcher-final/alignment.json for the exact gap.
