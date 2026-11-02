# Dependency upgrade state scope (2026-11-01)

The PMKID upgrade entry submits only pmkid=True. The common dependency checker always returns rows for every capture feature and marks omitted feature flags DISABLED. The selected-operation completion path previously merged those synthetic sibling rows into the connected-device snapshot, replacing previously verified READY/MISSING states. A later full-scope check restored the statuses. The reported historical upgrade itself has no captured diagnostic evidence; the state-overwrite mechanism was reproduced offline.

The public selected-operation result boundary now keeps capture feature rows only when the feature is selected or explicitly supplied in the recheck flags. It applies after both successful checks and failure/cancellation reprobes. This preserves unspecified siblings without hiding explicit opt-outs or genuine shared-dependency refreshes. Bulk operations are unchanged. The original operation error remains authoritative even if a recovery probe reports the target ready.

Scope exemption: this changes only local result publication. Router commands, command arguments, package selection, package install/remove behavior, probes and their timing are unchanged. No new router/version-specific behavior was introduced, so no new OpenWrt command compatibility claim is made.

Before the fix, four scope regressions failed with a previously READY scan row becoming DISABLED. After the fix, targeted upgrade-scope, group-control, hcx-upgrade and automatic-capture-upgrade-feedback checks passed. Coverage includes success, failure, cancellation, sparse single installs, explicit disabled flags, full feature refresh, task ownership, and error preservation. Runtime: Windows project virtual environment, Qt 6.3.2. Tests are offline; no router package was installed for verification.

Pending: Debian 12 x86-64 desktop and a real router upgrade. On the next legitimate hcx upgrade, record dependency statuses before/after and confirm only explicitly checked items change. Preserve upgrade logs if the device operation itself fails. Do not infer universal firmware/hardware support from these state-publication tests.
