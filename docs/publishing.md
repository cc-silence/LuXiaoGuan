# Publishing software and offline bundles

The owner's request to publish includes validation, upload, channel switching and public readback. This policy was confirmed on 2026-11-06 and supersedes the Cloudflare-only offline retention text in historical delivery reports.

## Destinations

- Software full ZIP: identical bytes on Cloudflare and https://github.com/luxiaoguan-ui/luxiaoguan-publish .
- New offline common/capture ZIPs: GitHub only. Keep every pre-existing Cloudflare offline object and channel unchanged for old applications.
- No application source, user configuration, test credentials or publisher credentials belong in the public release repository.
- GitHub public alpha assets are permitted, but the application must request only stable notices without a testing code. A valid code enables the signed alpha notice. The code is never sent to GitHub or its asset CDN.

## Routine commands

Run from RouterManager_python with the pinned .venv interpreter. Read project AGENTS.md first.

1. Increment main.py APP_VERSION once for a new software build. Run affected regressions and `.venv/Scripts/python.exe scripts/build_release.py`. Do not reuse an older ZIP after changing source. Do not add downloaded offline bundles to a normal software update ZIP.
2. Write release notes as UTF-8 (Chinese via Unicode escapes on Windows), then run:

```
.venv/Scripts/python.exe scripts/manage_publishing.py publish-app --channel alpha --archive <verified-software.zip> --notes-file <notes.txt>
.venv/Scripts/python.exe scripts/manage_publishing.py publish-offline --kind common --archive <verified-common.zip> --coverage <matching-coverage.json>
.venv/Scripts/python.exe scripts/manage_publishing.py publish-offline --kind capture --archive <verified-capture.zip> --coverage <matching-coverage.json>
```

Use `--channel stable` only for a stable version requested by the owner. Dependency-only publication does not require rebuilding unchanged dependency catalogs, but must verify actual ZIP/report/coverage and signed minimum application version. Never weaken a compatibility floor.

3. The coordinator verifies the dedicated publishing account/repository, uploads immutable files, anonymously hashes all public bytes and then switches the small signed channel notice. Software uses exactly the same signed notice on both hosts, containing a signed GitHub mirror. Channel notices are release assets on the `update-channels` tag. Immutable software tags are `app-<platform>-<version>`; offline tags include kind and full SHA256.
4. Validate current installed/previous clients against the new signed contract. Check no-code stays stable, wrong code fails, valid code offers alpha, proxy failure reaches GitHub direct, and the downloaded bytes match. Preserve all old Cloudflare offline objects and signed notices; the coordinator and retention policy protect them.
5. Record public URLs, versions, sizes, hashes, test scope and any unverified platform limitations. Receipts live under build/publishing. Offline publication refreshes docs/offline-packages/support.json and support.txt automatically.

## Failure handling

Never call a one-host result complete. Keep the existing receipt and reconcile the signed channel and immutable bytes before retrying. The publisher lock prevents concurrent local publication. GitHub immutable assets must match their existing digest; mismatches stop rather than overwrite. Mutable channel assets are backed up before replacement. TLS/signature/hash failures remain terminal.

Cloudflare is limited to 1,000,000,000 peak bytes by project policy, including protected legacy resources. Insufficient space must not cause deletion of old offline bundles, stable software or another platform's active release. Existing alpha maintenance/recovery rules apply only to its own slot.

GitHub publisher authorization is isolated in the Windows credential vault. Source Git remotes and accounts are unchanged. Do not run gh auth setup-git. The public release repo is initialized with a release-only README when empty; application source stays in its existing repository.

Windows publication must pass frozen package smoke checks. Debian 12 x86-64 publication additionally requires its native desktop acceptance. Shared transport tests on Windows are not Debian native validation.

Offline bundle checks/downloads keep the existing user-selected proxy/direct channels. The software CF-proxy-to-GitHub-direct policy must not silently disable offline download proxy settings.
