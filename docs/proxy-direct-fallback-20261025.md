# Proxy failure to direct fallback (2026-10-25)

The shared proxy route order is now configured HTTP, SOCKS, system proxy, then explicit direct. Duplicate proxy URLs are removed. With no configured proxy, direct is still available. Explicit direct mode continues to use direct alone. Existing configuration is not rewritten.

This applies to application update checks and files, common/capture offline bundles and ordinary software-item download tasks using the shared policy. Individual IPK/APK downloads already had a final direct route and retain their existing bounded retry rounds. Router-side opkg/apk networking is not changed by this PC-side policy.

Manual proxy switch buttons use configured_proxy_channels, which excludes direct. This preserves their previous availability behavior and avoids counting the automatic direct fallback as an actual configured proxy. No layout, theme, icon or label is changed.

Update checks skip unusable proxy addresses, retain a bounded total budget for all candidates, and use Qt per-request transfer timeouts. A Qt transfer timeout may report OperationCanceledError; explicit user cancellation still disconnects and aborts the request, and size-limit errors remain terminal. Offline bundle requests reserve connection/read budgets for remaining routes, including direct, inside the existing overall deadline. No new timer or polling loop is introduced.

Proxy construction errors, transport errors and HTTP 407/408/429/500/502/503/504 can move to another route. Authentication/access denials such as application HTTP 403, redirects, oversize responses, invalid signatures and content hash failures are still terminal. Switching routes never disables TLS, signature validation, or content limits in the update/bundle transports.

Direct requests use Qt NoProxy or requests Session.trust_env=False with an empty explicit proxy mapping. Script environment construction clears proxy variables and sets NO_PROXY=*. New software download tasks construct fresh network options rather than copying failed proxy options.

Validation includes real local Qt HTTP update responses after invalid proxies, connection refusal, and a stalled proxy; a real compressed update object goes through existing catalog validation and decompression/hash checks after proxy failure. Additional cases cover proxy authentication/gateway errors, empty proxy configuration, proxy deduplication, cancellation, remaining time budgets, manual proxy availability, and submission of ordinary software download tasks through direct. Production gateway reachability was not measured and no live router state was changed.

References:
- https://doc.qt.io/qt-6/qnetworkrequest.html#setTransferTimeout
- https://requests.readthedocs.io/en/latest/api/#requests.Session.trust_env
