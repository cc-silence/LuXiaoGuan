# Partial uninstall review (2026-11-04)

The shared dependency manager delegates independent removals to dependency_batch_uninstall.
Only a structured removal_blocked result is eligible for continuation. Opkg dependency refusals
are classified from native output; APK retention is recognized only from a parsed safe simulation,
an explicit not-removed header and its successful completion marker. Unsafe extra package actions,
unknown plans, permissions, database locks, timeouts, cancellation and connection replacement stop.
No force, recursive dependency removal or autoremove options were added.

Every batch reads the exact installed package inventory again. Removed and retained package names
are summarized separately. Unexpected remaining packages fail verification. Retention is reported
as partial_uninstall, with bounded native diagnostics and the refreshed dependency snapshot, rather
than a full success. All entrypoints through uninstall_packages share this behavior. Existing UI
components and appearance are unchanged.

Validation: 71 related offline regression tests passed, including seven new tests (with opkg/APK
subcases), native plan rejection, occupied first package, later independent removal, execute-time
dependency rejection, cancellation, timeout, disconnect, connection replacement, permissions,
database lock, unexpected remaining packages and service snapshot propagation. Python syntax,
UTF-8 replacement-character scans and git diff checks passed.

Source evidence: uninstall-sources-20261104.json maps to the existing exact 50-release baseline.
Seven fixed opkg revisions and two fixed APK revisions were inspected, alongside upstream APK 3.0.0.
No router package was removed during this work. Windows offline validation is not live-router or
Debian 12 validation. Debian validation pending: run the same offline suite on Debian 12, then
verify a supported router with one dependency-protected package and one independent package.

The previously delivered 1.0.40_alpha ZIP predates this fix and has not been rebuilt.

## Follow-up source and compatibility review

Revalidated SHA-256 for the exact 50 release Makefiles and their package-manager commit mappings.
Fetched nine additional exact execution sources: opkg_cmd.c at all seven opkg commits and commit.c
at both APK commits. Opkg removal errors propagate to a nonzero return. APK simulation suppresses
package installation and uses the explicit retention notice and successful summary checked here.
References and hashes are recorded in uninstall-sources-20261104.json under execution_sources.

Found and fixed one reporting gap: a later failure used to discard the names already processed.
An aborted batch now reports attempted removals (explicitly not finally verified), dependency-skipped
packages and packages whose completion is unconfirmed, while preserving the original exception type,
error code and bounded native diagnostics. No extra mutation or automatic retry is performed.
Added later-error and final-inventory-failure regressions for both native managers; 73 related tests
passed after this correction. No new Windows-specific imports, shell dependencies or platform APIs
were added. This does not constitute a Debian 12 run or any architecture/model live-router validation.

## Second follow-up review

Reproduced a mixed-error classification gap: native EPERM (Operation not permitted) or EIO
(Input/output error) together with an opkg dependency-refusal notice was eligible for continuation.
These system failures now take precedence over dependency retention in both batch classification
and user-facing error reasons. A mixed refusal does not continue to later removals.

Added tests for mixed native errors, cancellation/disconnection/connection replacement during the
final inventory read, and an APK retention notice combined with an unsafe unselected removal.
The final related regression run passed 76 tests. Existing platform and live-device limitations,
and the fact that the previously delivered ZIP predates the fix, remain unchanged.
