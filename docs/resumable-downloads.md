# Interrupted immutable downloads

Reviewed 2026-10-28. Common/capture offline archives and application update
objects share `modules/resumable_download.py`. No router commands, package
selection, installation, firmware support inventory, or interface styling change.

## Contract

- Partial paths are bound to signed content hashes and expected byte counts.
  Retry, cancellation, connection failure and process exit retain written bytes.
- Send `Accept-Encoding: identity` and a single open-ended byte Range.
  Validate 206 Content-Range start/end/total and Content-Length before appending.
  A 200 response replaces the partial instead of appending a full duplicate.
- Reuse a complete partial only after hashing. Reject bad ranges, redirects,
  encodings, oversized responses and bad final hashes. Discard corrupt complete
  partials; incomplete or unverified bytes never enter installation.
- Recheck partial size between routes, including a connection failing after its
  final payload byte. Keep bounded chunk memory and existing cancellation and
  request deadlines. Cached bytes do not inflate the displayed transfer rate.
- Existing signed update-stage cleanup owns update partials. Offline resume
  receipts authenticate ownership before removing superseded partial archives.
  Current partials and unowned files survive; keep one selected revision per kind.
- The gateway supports byte ranges for immutable update blobs as well as ZIPs,
  sends 416 for unsatisfied single ranges, and ignores unsupported multiple ranges.
  Nonmatching If-Range falls back to a full 200 response.

## References and compatibility

- [RFC 9110 sections 14 and 15.3.7](https://www.rfc-editor.org/rfc/rfc9110.html#section-14)
  defines Range, Content-Range, full-response fallback, and combining byte ranges.
  Signed SHA-256 identity binds immutable resources across requests; final hash
  verification remains mandatory even if a proxy returns incorrect content.
- [Cloudflare R2 Workers API: ranged reads](https://developers.cloudflare.com/r2/api/workers/workers-api-reference/#ranged-reads)
  supports explicit offset/length reads; the public gateway supplies HTTP headers.

Windows and Debian 12 share the same Python streaming and pathlib code; no OS
branch or Windows API is added. Local socket regressions run on Windows with
Python 3.10 and Qt 6.3.2. Debian 12 runtime and packaged executable testing remain
pending. OpenWrt versions and hardware are unaffected because this change only
transfers verified files to the desktop, before existing router installation.

## Regression coverage

`tests/test_resumable_download.py` uses a real HTTP server that disconnects after
128 KiB. Both download entry points must retain those bytes and request only the
remaining suffix in a fresh task. Coverage includes route fallback, ignored Range,
corrupt files, invalid range/size/encoding responses, cancellation, deadlines,
oversize protection and authenticated cleanup. Gateway Node tests cover blobs,
ZIPs, HEAD, suffix ranges, 416 and If-Range fallback. Existing signature, update,
proxy fallback, owned path and bundle lifecycle regressions also apply.

## Verified result

101 targeted Python regressions and 15 Node gateway tests passed, as did the
repository source check and Wrangler deployment dry run. The gateway was deployed
and public checks returned 206 for an existing signed update blob and both offline
ZIPs, plus 416 for an unsatisfied blob range. The sampled blob matched its signed
SHA-256 in full, and its suffix matched the resumed response byte for byte.
Evidence: `build/resume-final-tests.log` and
`build/resume-online-verification.json`. Client source changes are not yet built
or published as a new application version.
