# LAN readiness and diagnostic evidence, 2026-10-31

## Scope

The user feedback archives ending 060237 and 063906 contain an accepted LAN apply, a failed local DHCP operation and no successful authenticated verification before rollback. They do not prove that router configuration was never changed or that automatic rollback was verified. The second archive separately confirms successful homepage application followed by an unrelated signal-read failure.

The changes here add task-bound metadata and bounded local-address readiness. They do not change router commands, authentication, the native 90-second rollback boundary, force consent or router restart behavior. Detailed evidence remains in the existing background diagnostic store and is exported using the existing common-settings log entry. No new UI, free-text command logging or separate unbounded log file was added.

## Evidence

- Capture initial selected-adapter relationships, DHCP mode, selected route, connection state, usable-address counts, target-subnet/gateway matches and address conflicts. These records contain no raw IP, MAC, GUID, adapter name, credentials or wireless configuration. Initial cached review state is explicitly labelled; fresh observations follow the actual system operation.
- Preserve Windows native release/renew error codes separately from process exit codes, including an elevated attempt. Codes are numeric, not subprocess output. Linux retains the NetworkManager process return code without retaining command arguments or stdout/stderr.
- Record operation budget, automatic/manual origin, apply/restore phase, readiness observation counts, state transitions and expiry. Observations are deduplicated, task-scoped and bounded by the existing journal limits and seven-day common storage policy.
- Classify chained connection errors: timeout, refusal/reset/abort, unreachable network/host, DNS, authentication, host key change/confirmation and SSH channel closure. Unknown errors remain unknown; NAT or bridge mode is not inferred from an address or adapter vendor.
- Existing apply, confirmation, runtime mismatch and verified/unconfirmed rollback events remain distinct. Candidate indices and task IDs retain correlation without exposing endpoints.

## Readiness behavior

Execute the existing selected-adapter system operation once. On success, inspect for a usable address in the target network until ready, cancellation or the existing task deadline. A recognized temporary lease-renewal failure may still be followed by asynchronous address readiness; observe without resubmitting the OS request. Permission, identity, unsupported-method and reboot-required failures remain final. A new rollback phase invalidates an old renewal worker permit.

The readiness loop performs bounded read-only observations in a background worker; it does not add a UI timer, repeated apply, all-adapter reset, router reboot or another DHCP client. If expiry occurs, keep the last observed reason and the original system error when present. Client address readiness does not itself confirm the router configuration; authenticated router verification remains required.

## Sources reviewed

- Microsoft RenewDHCPLease: https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/renewdhcplease-method-in-class-win32-networkadapterconfiguration
- Microsoft ReleaseDHCPLease: https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/releasedhcplease-method-in-class-win32-networkadapterconfiguration
- NetworkManager nmcli: https://networkmanager.dev/docs/api/latest/nmcli.html

Microsoft documents distinguish success, reboot-required, unsupported, access-denied and lease-renewal/release failures. Both Microsoft pages were read through the web source reader; native cached downloads failed certificate validation, which was not disabled. The successfully cached NetworkManager reference and retrieval hash are under build/network-recovery-audit-20261031/sources/manifest.json. No router-side command was added or changed; prior exact rpcd apply/confirm/rollback source contracts remain applicable.

## Verification and limits

117 related offline test executions passed after restoring the optional Lua 5.1 simulator in build/wireless-test-runtime. The new diagnostic/readiness module passed 9 tests, including actual Windows PowerShell execution with fake CIM functions for success, release failure, renewal failure, permission denial and unsupported return codes. Counts overlap and are not combined.

Tests cover delayed readiness, a temporary OS error followed by readiness, wrong-subnet expiry, cancellation, diagnostic sink failure, chained connection errors, raw code preservation, export privacy, existing LAN apply/force/rollback and factory-reset recovery. No real router or adapter was changed. Debian 12 and real Windows DHCP transitions remain unverified; mocks are not live-platform acceptance.

## Two-pass review completed 2026-11-01

This review covers the uncommitted login dependency checks, single-item result isolation, check-progress completion, LAN client readiness and structured diagnostic changes. The contemporaneous hcx policy, bundle and primary-installer tests were included for cross-impact coverage; this is not acceptance of every hardware/package combination or a new audit of every supported firmware release.

### Round 1: source and call-path review

Confirmed and corrected:

- Debian readiness inspection unnecessarily queried every adapter through NetworkManager. The facade now passes the selected adapter identity and inspects only that device; the selected device can also be found beyond the first 128 enumerated entries. General inventory calls retain the original bound.
- A temporarily unknown DHCP state was treated as an explicit manual/static setting. Unknown state now remains pending; only an explicit DHCP-disabled result stops the wait.
- Failed dependency-install rechecks omitted the already known device information. Both recovery recheck paths now use the same device context as normal checks.

Round-1 result: 220 unique test cases, no failures/errors/skips. Report: build/network-recovery-audit-20261031/round1.json.

### Round 2: adversarial and compatibility review

Confirmed and corrected:

- A client adapter holding the router target address as an alias could still be marked ready. Address conflict is now a distinct pending/failure reason.
- An adapter identity change is now recorded explicitly and stops the stale wait immediately rather than consuming the remaining deadline.
- An address in the expected network is checked alongside on-link reachability or a proven route on the selected adapter. A valid broader subnet or an explicit host route remains accepted; a host-only mask with no route remains pending.
- The cached-state diagnostic flag was allowed by export but dropped by the platform emitter. It now survives both layers.
- A local address-wait timeout could be labelled as an SSH/socket timeout by generic management metadata. Connection-specific classification now requires connection evidence for an ambiguous timeout.
- Administrator authorization cancellation could be labelled as an ordinary lease failure and lose its return code. It now retains a separate authorization failure and the original numeric result.

Added tests also verify cancellation during inspection, old-phase worker rejection after rollback begins, diagnostic context release, disk/sink failure, native/elevated Windows return codes and preservation of existing force-application consent. One newly added test initially used an incomplete QObject fixture; the fixture was corrected to provide the production progress signal, then the complete suite was rerun.

Round-2 final result: 309 unique test cases, no failures/errors/skips. Report: build/network-recovery-audit-20261031/round2-final.json. The rounds overlap and their counts must not be added as distinct tests.

### Source evidence and practical limits

Reviewed Microsoft Win32 DHCP method return-code documentation, NetworkManager 1.42.4 command/exit-status contracts, the pinned rpcd apply/confirm/rollback implementation, opkg installed-list and APK 2/3 information contracts, and hcxdumptool 6.3.4 requirements. Five fixed-version upstream files were retained with SHA256 values in build/network-recovery-audit-20261031/sources/two-pass-sources.json. Existing Microsoft web-reader evidence and native-download certificate limitations remain as stated above; validation was not disabled.

Additional fixed-version references:

- https://raw.githubusercontent.com/NetworkManager/NetworkManager/1.42.4/man/nmcli.xml
- https://raw.githubusercontent.com/openwrt/rpcd/8d26a1ba3a175016b342cc5e25d340c08ae20eed/uci.c
- https://raw.githubusercontent.com/alpinelinux/apk-tools/v2.14.4/src/app_info.c
- https://raw.githubusercontent.com/alpinelinux/apk-tools/v3.0.0/src/app_info.c
- https://raw.githubusercontent.com/ZerBea/hcxdumptool/6.3.4/README.md

A read-only check on this Windows/Qt 6.3.2 host found four adapters and correctly returned the selected one in about 0.08 seconds. This is a local verification result, not evidence that the feedback user's failure was caused by multiple adapters or by wrong adapter selection.

All changed/new Python files in the working tree were compiled and scanned for replacement characters/mojibake, and git diff --check passed. Final hashes and static results are retained alongside the test reports.

No production router configuration, local DHCP lease or adapter state was changed. Real router LAN relocation/rollback, actual Windows DHCP transitions and Debian 12 runtime acceptance remain unverified. The old feedback lacks the original DHCP result and cannot establish its complete cause. The unrelated recurring SSH Channel closed root cause, and the previously identified homepage-follow-up signal refresh, are not declared solved by this review. No application build, release, commit or push was performed.

## Actual login adapter binding follow-up, 2026-11-01

The owner clarified that the feedback machine may have two Ethernet and two Wi-Fi adapters. This is a possible contributing condition, not proof of that incident's cause. The old archive contains no sufficient adapter-selection/lease result evidence to establish wrong-interface renewal.

The prior inventory used a system route estimate (a UDP source-address lookup on Windows and ip route get on Linux). It was not explicitly tied to the established SSH TCP connection. Inventory now reads the actual transport socket's local endpoint and matches that IPv4 address to current adapter ownership; it does not choose the first adapter or prefer Ethernet/Wi-Fi by name. The peer remains the established connection's remote endpoint.

Automatic recovery requires one connected, DHCP-enabled, renewable adapter supported by actual SSH socket evidence. Missing/invalid/IPv6 local endpoints or unsupported socket-like proxies fall back to read-only route guidance and manual recovery; an estimated route is not sufficient for an automatic lease operation. Duplicate ownership is ambiguous and never triggers automatic renewal. The existing GUID/index/MAC or NetworkManager profile revalidation still protects the subsequent OS operation.

The export records only selection_source, route_candidates and automatic_allowed together with existing task context. It does not retain the socket's local IP, adapter name, MAC or GUID in the new diagnostic records. This does not attempt to infer NAT/bridge mode.

Official references checked: Paramiko 5.0.0 Transport stores the underlying socket and implements getpeername through it; Python 3.10 socket.getsockname returns the local endpoint. Source URLs:

- https://raw.githubusercontent.com/paramiko/paramiko/5.0.0/paramiko/transport.py
- https://docs.python.org/3.10/library/socket.html#socket.socket.getsockname

The final follow-up suite passed 316 unique cases with no failures/errors/skips. Added cases cover four adapters with an incorrect route estimate, duplicate source-address ownership, a closed socket, an unsupported proxy socket, a missing source owner, invalid/IPv6 sources and export privacy. Report: build/network-recovery-audit-20261031/binding-final.json. These are offline cases, not a live replication of the user's VM/router setup. The earlier live-device and packaging limitations still apply.
