# Mirror preferences, 2026-11-03

Default direct order: USTC, Aliyun, Tsinghua, NJU. Proxy routes retain official URLs.
One installation session shares successful-mirror preference and bounded HTTP 403/451
counters across package downloads and native temporary source selection. Two denials
lower a mirror's priority; nothing is permanently disabled. HTTP 404 does not blacklist
a site. State and proxy bridges are released at task completion. Summary messages
include source names and HTTP status while exported diagnostics retain endpoint detail.

Aliyun operator reference: https://developer.aliyun.com/mirror/openwrt
Live read-only probes returned 200 for OpenWrt 23.05.4 base/Packages.gz; sampled
24.10.8 and 25.12.6 index paths returned 404. Missing files continue to the next mirror.
Aliyun packages-24.10/aarch64_cortex-a53/packages/hcxdumptool_6.3.4-r1_aarch64_cortex-a53.ipk
returned 37672 bytes, SHA256 6dc939a45c67a5afe41e4489dd3fa2c5f6bd6086a61dcdc853f925538f428364.
The pinned project body is 37681 bytes; it is a different file. The fixed 24.10.8
Aliyun package path returned 404. No package was installed during these probes.

Tsinghua's current 403 response was read using the app User-Agent. Its generic
page lists uncommon-client characteristics and subnet request history as possible
causes. This does not establish which rule caused the historical rejection.

Mirror and installation regressions: 158 targeted tests passed before adding the
separate alternate-hcx confirmation feature. No router source files were changed,
and no package integrity check was disabled by these routing changes.
