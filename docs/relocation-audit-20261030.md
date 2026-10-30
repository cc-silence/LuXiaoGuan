# Relocation cleanup audit - 2026-10-30

Read-only production-code audit. Production Lua was executed against controlled device responses; no live router configuration was changed.

## Reproduced defects

1. relocation_networks.py:10: an unrelated static wired interface named wwan8 on eth2 is previewed and deleted despite having no wireless references. The existing single-signal deletion path preserves it. Name matching must not substitute for ownership/topology proof.
2. relocation_commands.py:44 and signal_relocation_actions.py:66: the initial preserve preview requires AP MAC editing capability before displaying the choice dialog. If that capability is absent, users cannot reach the discard button, although a direct discard preview succeeds.
3. relocation_commands.py:202: an old AP remains listed in wireless status, but its device-status RPC fails. An empty fallback response is accepted as evidence that the interface stopped, and apply confirms success. This demonstrates a verification gap, not proof that every affected AP remains transmitting. Unknown state must not count as verified shutdown.

Reproduction results: build/relocation-audit-20261030/reproductions.json.

## Compatibility

Fixed-release source checks covered 50 releases across OpenWrt 19.07, 21.02, 22.03, 23.05, 24.10 and 25.12. Existing shared verification handles older hostapd responses without get_status and early responses without SSID. Source checks do not certify every firmware or router.

The 25.12 ucode AP script maps configured MAC and SSID to hostapd settings. Device-driver support still requires runtime verification.

DFS is a conditional limitation: the current reconnect allowance is about 82 seconds, with a 90-second rollback window. Regulatory channel availability checks can require 60 seconds or 10 minutes. The longer check cannot complete within this transaction window. Actual hardware reload behavior remains untested. Address this through preflight policy and transaction design, not timer-only padding.

Source inventory and hashes: build/relocation-audit-20261030/sources.json.

## Primary references

- https://raw.githubusercontent.com/openwrt/rpcd/8d26a1ba3a175016b342cc5e25d340c08ae20eed/uci.c
- https://raw.githubusercontent.com/openwrt/netifd/5e02f94411b06f192fb2a7d9be9abde3549153a8/wireless.c
- https://raw.githubusercontent.com/openwrt/openwrt/v25.12.0/package/network/config/wifi-scripts/files-ucode/usr/share/ucode/wifi/ap.uc
- https://wireless.docs.kernel.org/en/latest/en/developers/dfs/etsi.html

Fix the three reproduced defects and cover them with regressions before live cleanup acceptance. Prior passing tests did not cover these cases. Production code was not modified during this audit.
