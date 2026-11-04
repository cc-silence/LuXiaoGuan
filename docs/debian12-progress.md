# Debian 12 adaptation progress

Checked on 2026-10-28. Development branch: feature/debian12-runtime.
Target confirmed by the user: Debian 12 desktop, Intel/AMD x86-64.
The user has not installed Debian and has declined WSL. Do not install, enable,
or reboot into a Linux/virtualization environment as part of unattended work.

## Completed source stages

1. Private CPython 3.10 / PySide6 6.3.0 admission and platform identity boundaries.
2. XDG configuration, state and work-data directories.
3. Startup firewall applicability, shutdown dispatch and Linux desktop shortcuts.
4. Persistent keyring backend, isolated helper protocol, asynchronous login,
   remember/forget and router-password persistence.
5. Native trash dispatch, Linux filename/case semantics and atomic no-replace
   publication across file transfer and export entry points.
6. Windows-free process-service import, Unix single-instance ownership and
   explicit prevention of use of the Windows native updater on Linux.
7. Reviewed synchronization of Windows 1.0.18_alpha changes: 33 files, with four
   conflict resolutions preserving Linux behavior. No Windows executable was
   copied as a Linux implementation.
8. Four independent release slots, current-only ZIP retention, quota-only alpha
   replacement with durable resume, deployed gateway target separation and
   actual removal of the unreferenced old 1.0.16_alpha ZIP.
9. Recover the exact source of the existing terminal (502 matching fingerprints),
   portable symbol exports, a Qt 6.3.0 exact CMake entry, Linux library admission,
   native build/smoke entry and failure-before-replacement protection.

10. Native result-reference paths and isolated Linux evidence metadata checks;
    preserve unknown history, directory/mount identity and Windows path behavior.

11. Synchronize 173 distinct source files through committed Windows 1.0.21_alpha
    (f38c3fcf), reviewing merge conflicts and retaining the Linux platform services.
    Add a read-only guard reporting committed drift and separate pending Windows
    edits, including changes that do not increment the application version.
12. Verified official private Python source, isolated interpreter build tooling and
    a manual Debian 12 container workflow. No native compilation has run here.
13. Linux update catalog validation and archive preparation: retain executable
    permissions, case-sensitive paths and bounded internal file links.
14. Stable old-runtime snapshots for a future updater, using verified hardlinks or
    bounded streaming copies, cancellation, timeouts and durable metadata.
15. Debian-only candidate build entry, source/entrypoint bytecode checks, Unix ZIP
    verification and independent candidate retention/failure cleanup. A busy update
    lease now prevents snapshot writes. Native compilation remains unperformed.
16. Linux installation planning, durable replacement/recovery transactions and
    directory-fd filesystem operations. Unknown/concurrent edits stop recovery
    without overwriting them. The live worker/startup adapter remained unwired at
    this stage; stage 17 adds those standalone components.
17. Signed Linux worker jobs, pidfd parent waiting, bounded startup probes, stable
    recovery launcher, early frozen-main dispatch and package/shortcut selection.
    Heavy preparation runs separately from the short launch commit. The desktop
    download/controller and publisher still do not enable the Linux update path.

Stages 1-6 have individual documents. See debian12-stages7-9.md,
debian12-stage10.md, debian12-stages11-14.md, debian12-stage15.md and
debian12-stage16.md and debian12-stage17.md for later work.

## Isolation and Windows impact

Linux desktop, credential, filesystem and terminal changes remain in this worktree.
The shared publication/manifest/maintenance-response rules and their tests were
intentionally synchronized to the active Windows checkout, including recovery
backups. Both projects' rules now record the confirmed x86-64 target. This is not
an application release, a version bump or a merge of all Linux work into Windows.
The latest reviewed committed Windows baseline is 1.0.21_alpha at
f38c3fcf2a9ffd2d8d047b2fcbf40c33ef39bd8a. Pending Windows source edits are
reported separately and are not automatically copied. Stages 11-14 did not change
active Windows application source, publish packages or dispatch a cloud workflow.

## Verification and evidence

- Previous complete offline audit: 159 modules, 1288 collected tests; 1286 ran and
  passed, two explicit skips. This predates later publication/terminal additions.
- Later release audit: eight modules, 58 tests, all passed.
- Stage 9 targeted audit: seven modules, 55 tests, all passed, including quota
  recovery, publication integration, retention, runtime and terminal admission.
- Stage 10 targeted audit: four modules, 37 collected, 35 executed passes and
  two native Linux skips on Windows. Actual Linux evidence checks remain pending.
- Windows shared publication tests: 13 passed in the active Windows checkout.
- Existing Windows terminal DLL actually loaded and passed its output, split
  Chinese UTF-8, alternate-screen and keyboard-input smoke after loader changes.
- Gateway: 14 synthetic Node tests passed. Anonymous live health, Windows/Linux
  routing and invalid-target checks all passed. No live maintenance interruption
  or protected-alpha testing-code request was performed in this validation.
- Source policy and diff formatting checks passed. Modified Python files compiled
  immediately; text replacements and encoding were checked.

- Current platform audit: ten modules, 94 collected, 92 executed passes and two
  native Linux skips on Windows. This includes final catalog/snapshot hardening.
- Latest committed Windows theme/deletion/policy synchronization: eight targeted
  modules, 83 tests passed. Snapshot tests subsequently increased from seven to
  eight and passed in the current platform audit.
- Configuration chain: six modules, 75 tests passed; storage/release chain: nine
  modules, 78 tests passed. A monitor subprocess timed out during concurrent
  archive extraction; the unchanged module passed all 11 cases in isolation.
- Stage 15 audit: 14 modules, 127 collected, 124 executed passes and three native
  filesystem skips under Windows. After final failure-cleanup refinement, six
  affected modules collected 50 tests with 49 executed passes and one symlink skip.
- Stage 16 final audit: eight modules, 90 collected, 82 executed passes and eight
  native Linux skips on Windows, including 25 transaction cases. The six native
  directory-fd cases were not run on Windows and must pass in Debian CI.
- Stage 17 final audit: 15 modules, 144 collected, 133 executed passes and 11
  native Linux skips. This includes 15 signed-worker cases, 11 recovery/launcher
  cases and existing Windows startup, shortcut and update regressions. Shell
  routing ran under explicit Git Bash in Chinese/space paths, not under Debian.
- These are targeted audits, not a new complete-suite or native Debian claim.

Evidence under build/debian12:
- stages4-6-complete/summary.json
- stage8-release-audit/summary.json
- stage9-terminal-publishing/summary.json
- windows-sync-result.json and windows-publishing-sync.json
- cloud-cleanup-result.json and cloud-current-validation.json
- gateway-deployment.json and gateway-live-validation.json
- terminal-source-recovery.json
- stage10-result-presence/summary.json
- stage11-sync/{plan.json,applied.json,configuration-review,storage-review,monitor-recheck}
- stage12-update-contract/summary.json
- stage13-sync/{plan.json,applied.json,alignment.json,review/summary.json}
- stage14-final-platform/summary.json
- stage15-release-preparation/summary.json and stage15-release-final/summary.json
- stage16-linux-transactions-final/summary.json
- stage17-worker-launcher-final/summary.json
- source-layout-validation.json and source-cache/Python-3.10.22.tar.xz

A Windows offscreen smoke or a mock Linux branch is not Debian acceptance.
Earlier failed audit attempts are diagnostic history, not the current result.

## Stage 18 source refresh

- Source now matches committed Windows 1.0.22_alpha at aed7da0a. This stage applied
  79 reviewed shared paths plus the two-path upload follow-up, preserving Linux
  workers/platform services and excluding Windows updater binaries.
- PySide6/Qt 6.3.2 now drives Linux runtime, exact native SDK and candidate checks.
  Private Python 3.10.22+ remains required; native SDK provisioning is pending.
- The user approved matching error borders and reset recovery UI. Both themes and
  100/150 percent scaling were rendered using synthetic fixtures on Windows.
- 36 selected modules / 285 collected cases passed without skips under isolated
  Windows Qt 6.3.2. This is not native Debian acceptance or a full repository audit.
- See docs/debian12-stage18.md and build/debian12/stage18-sync/summary.json.

## Stage 19 source completion

The Linux desktop download/planner, safe independent updater handoff, common
progress UI, worker/cache lease coordination, signed Linux package preparation,
publication selection, and normal-user installation/uninstallation are connected.
Updater diagnostics use the shared platform log directory. See
`docs/debian12-stage19.md` for scope, evidence and native commands.

Final stage 19 audit: 38 modules, 332 collected, 310 executed passes and 22
expected native Linux skips on Windows. Source policy, syntax, workflow selection
and diff checks passed. No Debian workflow, package build or cloud mutation ran.

The committed Windows baseline is now 53af1454 (same tree as aed7da0a).
Uncommitted Windows source changes are listed separately by the synchronization
report and have not been automatically merged. The active Windows source was not
modified by this Linux work.

## Remaining release gates

- Establish a real Debian 12 x86-64 build/test desktop; none exists here.
- Provision the accepted private Python 3.10.22+ and exact Qt 6.3.2 SDK, validate
  wheels, compile/load the terminal and build a frozen candidate. Runtime migration
  remains deferred by user choice.
- Execute native system-call, install/uninstall and packaged startup checks. The
  builder requires nine native modules with zero skips before writing update
  admission; the current source has no such record and updates remain unavailable.
- Exercise two real package versions through upgrade, failed validation, rollback
  and interrupted-update recovery. This has not been performed on Debian.
- Validate keyring persistence/lock/timeout, trash, permissions, no-replace moves,
  shortcuts, instance ownership, Chinese fonts, tray, scaling and X11/Wayland.
- Exercise real SSH terminal and applicable workflows with authorized routers.
- Review and finalize any later Windows changes before merging them into Linux.

No certified Linux build or native installer archive has been produced. The source
implementation is ready for the above native validation; local model regressions
and Windows offscreen checks do not certify Debian behavior.


## Latest parity review: stage 21 (2026-10-30)

See docs/debian12-stage21.md. Source parity is now reviewed against the current
Windows 1.0.27_alpha working snapshot, with exact-file fingerprint checks.
Complete-ZIP updating replaces ordinary Linux incremental publication. New capture
scripts/tools are mandatory package assets. Native update admission now requires
ten modules with zero skips. The earlier native-validation checklist remains open;
there is still no certified Debian package or desktop acceptance result.
