# Three-round legacy IPsec review - 2026-11-04

Scope: only the newly added Windows local network-policy compatibility path.
No router-side command was changed; the OpenWrt matrix is therefore outside scope.
No live computer policy was modified, no service was stopped, and no package built.

## Round 1 - native contracts, identification, and preservation

Checked Microsoft netsh syntax, service status/configuration contracts, legacy local,
domain and persistent policy locations, and the existing active-policy identity.
Fixed:
- TCP expressed as numeric protocol 6 was incorrectly rejected.
- Original rule references were compared only by their last path component, which
  missed a change of policy store. Full references and registry types are checked.
- Windows object/key names are case-insensitive. Case-only policy name duplicates
  and owned-name collisions now stop mutation; GUID key casing is accepted.
- Registry value, per-node and whole-tree memory limits now apply during traversal,
  including a single key containing many large values and oversized multi-strings.
- Invalid target input is bounded and not echoed in errors.

Independent command-language fixtures are hand-authored from documented syntax;
they are NOT represented as output captured from a real populated IPsec store.

## Round 2 - transaction, backup, output, and failed receipts

Checked Microsoft directory security behavior and Python 3.10 subprocess/locale
contracts. Found an actual compatibility failure: locale.getencoding() was added
in Python 3.11, while this checkout runs Python 3.10.11. Removed that dependency.
Strict candidate decoding covers Unicode and actual Windows ANSI/OEM code pages;
every candidate must pass the entire exact endpoint verification, with no lossy
replacement and no successful status merely because text could be decoded.

Reused the existing bounded process runner instead of buffering unlimited output
and checking its length afterwards. Mutation commands no longer decode irrelevant
status text; dump verification receives raw bytes. Native command exit codes are
retained, while raw command output (which may contain policy secrets) is not logged.

Receipts are now atomic, flushed commits before mutation, after each successful
step, and at completion. A final receipt failure triggers recovery instead of
leaving active rules while reporting an unqualified failure. Failure records retain
the backup location and explicit journal/rollback errors even if disk writes fail.
Backup creation additionally checks the volume supports persistent security ACLs;
a nominal successful CreateDirectory call on a filesystem without ACL enforcement
would otherwise not establish the promised administrator-only protection.

Tested failures before and after each command's side effect, failed final receipt,
all receipt writes failing, atomic replace failure, bounded noisy-child output,
duplicate names, and detection of unrelated changes preventing unsafe recovery.

## Round 3 - integration, caching, diagnostic export, and platform boundary

Found startup completion was cached independently of firewall service mode. A
computer switching from disabled firewall to ordinary firewall could skip its
ordinary firewall audit. Audit results now carry the route; changing routes
invalidates the platform cache and triggers a login preflight re-audit. Same-route
logins retain cached behavior. Failed re-audits stop connection preparation.

Verified native/authorization errors reach bounded, sanitized ZIP diagnostics;
self-test administrator hosts cannot dispatch policy mutations; non-Windows routes
do not call Windows service/registry/COM APIs. Test-only Windows dependencies are
mocked at their platform boundary rather than required during pure regressions.

## Final validation

- 117 targeted tests passed using Python 3.10.11 on this Windows machine.
- Python compilation, UTF-8/replacement-character scans and git diff whitespace
  checks passed for reviewed changes.
- Read-only native probes passed for the bounded netsh show-store invocation and
  the backup volume's persistent-ACL capability. Previous service/join-state probes
  remained read-only. No real IPsec filter was added, deleted or assigned.
- Actual disabled-MpsSvc legacy-policy mutation, populated localized dump variants,
  runtime filter activation and real SSH connectivity remain unverified.
- Debian 12 native execution was unavailable: import/dispatch boundaries were
  checked with mocks; this is not a claim of Debian machine certification.

## Sources

The evidence index is docs/legacy-ipsec-review-sources-20261104.json. Official pages
were actually read using the web tool. Eight source snapshots were also archived
under build/ipsec-review-20261104/sources with SHA256 and fetch metadata. Direct
Python HTTPS archival of Microsoft Learn pages failed certificate validation;
Microsoft-owned GitHub documentation sources were archived instead where available.
TLS validation was retained. The old French legacy-policy-location page was read
via the web tool but could not be archived; that gap is recorded in the index.

Primary references include Microsoft netsh IPsec, QueryServiceStatusEx,
QueryServiceConfigW, CreateDirectoryW, legacy IPsec troubleshooting, GetACP, and
Python 3.10/3.11 locale and subprocess documentation. Source revisions are the exact
archived bytes and their SHA256; moving documentation URLs are not represented as
pinned upstream Git commits.
