# Stage 16: Linux installation transaction and recovery core

Checked: 2026-10-28. Target: Debian 12 desktop, Intel/AMD x86-64.
This is source implementation with Windows-hosted policy tests, not a Debian
installation, native acceptance or an enabled automatic updater.

## Scope and compatibility

No Windows test checkout source, application version, interface, cloud package or
router configuration changed. This stage operates on local managed application
files only; OpenWrt version/hardware dispatch is not involved. Windows keeps its
existing installer and transaction implementation. The public no-replace rename
function gained optional directory descriptors; its previous calling convention
and Windows behavior are preserved and covered by existing file/update tests.

References checked before implementing the native operations:
- https://man7.org/linux/man-pages/man2/rename.2.html
- https://man7.org/linux/man-pages/man2/fsync.2.html
- https://docs.python.org/3.10/library/os.html#files-and-directories

## Responsibilities

modules/platform_services/linux_update_files.py owns Linux file operations.
It opens every directory component relative to an existing descriptor with
O_NOFOLLOW, including when opening the installation root. It reads regular files
without following leaf links, streams hashes/copies, enforces size limits and
flushes files plus affected directories. This layer is guarded against execution
on Windows. The root descriptor stays bound even if its original pathname moves.

modules/app_update_services/linux_install_plan.py owns installation policy.
It validates Linux schema 2, version/resource advancement, retired-file ownership
and the installed baseline. Changed unknown files and locally modified managed
files are conflicts. Unchanged official theme/image/script resources can retain
local customization. Configuration and unknown user files are outside this
managed inventory and are not copied, deleted or migrated.

modules/app_update_services/linux_transaction.py owns the durable state machine.
It uses only the RouterManager update cache, checks the install lease, binds the
stage to the canonical incoming catalog hash and records the installation inode
identity. Each journal ends with its new baseline, whose hash must match the stage.
Recovery validates journal paths and content states before modifying anything.
Legacy WinBox cache locations are rejected before a lease file is opened there.

## Replacement and recovery sequence

1. Inspect ownership/version and verify staged regular file hashes/sizes plus the
   Linux x86-64 executable header before changing installed files.
2. Persist journal and active transaction. Prepare checked adjacent replacements
   with correct modes and internal links. The application must already be stopped.
3. Park each old file under its recorded adjacent name using no-replace rename;
   publish its prepared replacement with another no-replace rename. Original
   inodes are never truncated, so existing old-runtime hardlinks remain intact.
4. Run the caller's startup validation, then recheck all changed installed states
   before recording completion. The future worker must enforce the validation
   subprocess deadline; this core does not launch or kill the application itself.
5. On ordinary failure/cancellation, restore original files and baseline in reverse
   order with an independent recovery deadline. On abrupt interruption, reconcile
   actual target/parked/prepared states against the recorded journal. Completed
   commits continue cleanup rather than reverting a validated new version.

Each rename is atomic; the parking/publication pair has a short missing-target
interval. A stable external recovery launcher and parent-stop/startup admission
are therefore required before the live updater can use this core. Do not advertise
this internal component as a whole-application atomic switch or launch the app
between its replacement steps.

Recovery never overwrites an unexpected current file or invents a missing backup.
Concurrent edits, damaged backups or unverifiable sidecars leave active recovery
state and preserve evidence for reconciliation. Ordinary incomplete copy errors
remove only the newly created temporary inode. A power loss during an incomplete
copy can leave an unverifiable partial sidecar; it is preserved and requires
reconciliation, while the old installed files remain intact. Empty new managed
directories can remain after rollback; existing user directories are not removed.

## Verification

build/debian12/stage16-linux-transactions-final/summary.json contains eight modules,
90 collected tests, 82 executed passes and eight explicit native Linux skips:
- 25 transaction policy/recovery cases passed using a Windows IO model. These cover
  fresh installation, successful update, retired files, failed startup rollback,
  cancellation, missing/changed/unowned files, cache ownership, damaged downloads,
  version rejection, journal binding, concurrent recreation and content changes,
  file/link transitions, old hardlink preservation, interruption after parking or
  publication, and interruption after a completed commit.
- Six directory-descriptor/no-follow/fsync cases are Linux-only and were skipped
  on this Windows host. The model tests make no claim to verify those syscalls.
- Existing native no-replace and archive symlink cases account for the other two
  Windows skips. Existing Windows update and local-file tests passed.

In the manual Debian workflow, the transaction suite selects actual RootedFiles
instead of the Windows model, and all six native syscall cases must run. The
workflow now selects 16 modules and remains manual only. It was not dispatched.
Synthetic ELF headers used by these tests are never executed.

## Remaining integration

The live Linux updater guard is unchanged and remains closed. A worker adapter
still needs signed-manifest/catalog verification, staged downloads, parent-process
identity/waiting, an isolated old runtime, bounded native startup validation and
safe restart. Startup must reach a stable recovery entry even if installed files
are interrupted or missing. Linux publisher preparation and the user-facing
installation flow remain separate work. Custom retained resources must also be
reconciled with old-runtime snapshot admission before that adapter is enabled.
Actual Debian tests must exercise disk-full, permissions, interruptions, package
installation/update/recovery and desktop behavior before any Linux release claim.
