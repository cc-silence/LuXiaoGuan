# LAN client recovery audit - 2026-10-26

Scope: new client recovery planning, selected-adapter commands, shared countdown UI and the existing native router transaction. Two passes reviewed official API semantics and injected execution/lifecycle failures. No live adapter mutation or router write was performed.

## Findings and fixes

- Recovery offered only 25 seconds while claim required 30 seconds. Claims now require 15 seconds and reserve eight seconds for router reconciliation; expired requests publish their reason and current availability instead of leaving a silently disabled button.
- Pre-releasing a Windows DHCP lease could leave the interface without connectivity if renewal failed or the worker timed out. Windows now requests only instance-specific RenewDHCPLease and never pre-releases the lease. A successful method response still requires client and authenticated router verification. A failed renewal is reported rather than forcing an adapter reset. This is not a guarantee that every DHCP server responds before the rollback deadline.
- NetworkManager device disconnect disables automatic activation, and software devices may disappear. The implementation now reactivates only the pinned UUID and interface using connection up, without down/disconnect or profile modification.
- Validation time and post-operation inspection were outside the action budget. The facade now reserves final inspection time; Linux validation and activation share one deadline. An expired Windows authorization is not launched; the script independently checks its absolute expiry before mutation.
- In ambiguous multi-adapter plans, an unselected updated adapter could satisfy readiness. After a request, only the selected adapter may satisfy readiness. A verified SSH peer outside the reviewed old LAN addresses also suppresses unrelated local renewal, including overlapping WAN subnets.
- DHCP addressing can coexist with manually pinned gateway/DNS. Preflight now explicitly describes the necessary manual correction, and rollback includes original DNS information. Missing inventory still retains known original/target router addresses.

## Evidence and limits

84 related offline regressions passed, followed by 28 focused regressions after the final guidance changes. Eight edited Python sources compile and the Windows action script passes the actual PowerShell parser. Windows native inventory was checked read-only. The tests include real local Qt worker lifecycles and Lua apply/confirm integration with mocked OS mutation.

The client operation adds no OpenWrt CPU/driver/package-specific dependency. Existing native rpcd contract fixtures cover the project's official 19.07, 21.02, 22.03, 23.05, 24.10 and 25.12 branches; this audit does not certify individual routers, wireless drivers or a successful DHCP exchange. Actual LAN changes, DHCP renewal and UAC acceptance on Windows, and Debian 12 desktop/network-manager runtime remain untested. Linux managers other than NetworkManager retain manual guidance. Debian 12 X11/Wayland behavior is not established by Windows rendering.

## Official references

- Microsoft ReleaseDHCPLease (documents immediate connectivity loss): https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/releasedhcplease-method-in-class-win32-networkadapterconfiguration
- Microsoft RenewDHCPLease (method and return codes): https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/renewdhcplease-method-in-class-win32-networkadapterconfiguration
- Debian 12 nmcli manual (UUID/ifname activation, wait and disconnect semantics): https://manpages.debian.org/bookworm/network-manager/nmcli.1.en.html
- NetworkManager manual: https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli.html
- Red Hat NetworkManager reactivation examples: https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/8/html-single/configuring_and_managing_networking/index
- OpenWrt rpcd implementation: https://lxr.openwrt.org/source/rpcd/uci.c
- Exact supported-branch contracts: tests/fixtures/official_checked_apply_contracts.json
