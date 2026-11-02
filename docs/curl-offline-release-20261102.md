# curl offline release - 2026-11-02

Application: 1.0.35_alpha, Windows x64.
Common bundle: RouterManager_OpenWrt_Common_Offline_20261102_222113.zip.
Archive SHA-256: 3dfd646dc0ee940948611d8f95c74869f22acbc0fa75f908a69c6533406f8217.
Archive bytes: 196707660; baseline: 164146362; increase: 32561298.

All 549 previously installable release/target/architecture/package-manager identities
are preserved, across 45 installable official releases and 10 architectures. Every
profile now contains curl and its complete dependency graph. The existing capture
bundle already matches the published capture channel and is not republished.

The application now permits curl through the shared offline installer. Older signed
common catalogs missing the newly required root are rejected with an explicit request
to update the offline bundle. No extra roots or broken catalogs are accepted.

Official metadata uses versioned opkg provider aliases in name=version form. The
shared dependency rule parser now recognizes that syntax and checks the advertised
provider version. Its behavior is covered by regression tests.

Old recorded hashes for some 24.10 packages no longer match upstream bytes. All 81
24.10 release/architecture metadata sets were refreshed against current official
indexes; existing baseline packages were retained. APK profiles were re-resolved
against consistent current official indexes. No checksum or size check was bypassed.

Validation: syntax and encoding checks; offline download/install/update regressions;
39 final login-install/provider/curl tests; all 549 official platform identities;
2902 archive objects; saved and runtime dependency resolvers. Windows frozen build
passed source comparison (562 modules), all-pages/settings smoke, SSH dependencies,
native Qt terminal, update worker and local firewall helper self-tests. An actual
1.0.28 client accepted the complete new ZIP (882 managed files). No live router
installation was performed.

Publication evidence is recorded under build/curl-offline-release-20261102 and the
standard build/publishing manifest directories after upload/readback verification.

## Completed publication

Both application and common-offline channels were published and independently
read back with verified signatures and complete ZIP hashes. Live application
version: 1.0.35_alpha. Live common bundle SHA-256:
3dfd646dc0ee940948611d8f95c74869f22acbc0fa75f908a69c6533406f8217.
The actual frozen application also accepted the new signed offline catalog and
rejected the previous catalog with an update prompt.
