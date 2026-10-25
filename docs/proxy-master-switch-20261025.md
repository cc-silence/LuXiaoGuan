# Proxy master switch (2026-10-25)

The proxy settings page now exposes the saved boolean `enabled` as a shared-style checkbox. Unchecking disables the four editor fields and retains their text. In the current immediate settings window, toggling saves immediately. Reopen and imported configurations restore both the checkbox and input availability. Failed writes preserve the previous effective configuration and show the existing save error message.

Disabled means no application HTTP/SOCKS endpoints and no environment/Windows system proxy discovery. `configured_proxy_channels` and dependency download candidates return no proxy routes; shared download policy still supplies explicit direct. Application update and offline bundle transports already use Qt NoProxy or requests trust_env=False for direct. Enabling retains the configured HTTP/SOCKS/system/direct fallback order. Existing configurations without the new flag remain enabled to preserve historical behavior; the switch itself is stored as a real JSON boolean.

Router-side opkg/apk uses the router's own network. This PC setting does not edit router proxy/environment configuration. Existing in-flight connections are not forcibly interrupted; newly selected network routes use the saved switch. This change is independent of OpenWrt release, target, ABI, radio driver and package format; all supported releases use the same PC-side policy. It does not guarantee production server reachability.

New regressions cover disabled endpoint retention, disk reload, legacy migration, invalid dormant input, system/environment proxy suppression, UI auto-save and import. Actual settings renders cover both themes at 100/125/150/175 percent and both switch states in build/proxy-master-switch-review.
