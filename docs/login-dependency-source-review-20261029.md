# Login dependency installation: official-source review (2026-10-29)

This review covers the shared installation path used after login consent and by manual install buttons. No router was contacted or modified. No desktop visual behavior changed.

## Finding and retained fix

The feedback requested aircrack-ng, reaver and wpa-cli in one task. The local selector accepts roots from one signed bundle kind at a time. The combined request therefore missed usable capture and common repositories. The shared installer now partitions roots using the existing bundle definitions, rechecks later groups, and preserves the same local -> router network -> computer download routes for each group.

No additional confirmed implementation defect was found in this source review. This does not establish live-device installation success.

## Refreshed primary evidence

Fetched all 50 exact release Makefiles again; their SHA256 values match the previous immutable release evidence. Fetched 42 pinned native source files across 9 source revisions. Source URLs, SHA256 values, sizes and checks are recorded under build/review/login-dependencies/official-source-review-20261029/.

| OpenWrt family | Exact release build files fetched | Package manager |
| --- | ---: | --- |
| 19.07 | 11 | opkg |
| 21.02 | 8 | opkg |
| 22.03 | 8 | opkg |
| 23.05 | 7 | opkg |
| 24.10 | 9 | opkg |
| 25.12 | 7 | apk |

The device probe still selects the actual package manager; release numbers do not select execution commands. Exact firmware/target/package-architecture matching and existing candidate policy remain unchanged. Hardware, radio and driver support were not expanded by this change.

## Contracts checked

- opkg --noaction is recognized by the pinned native command parser. Existing operation guards reject changes to already-installed packages, while dependency resolution retains version conditions.
- opkg local transactions use an isolated -f configuration plus OPKG_CONF_DIR to avoid loading the device online feeds. Local package identities and hashes remain checked.
- APK repositories-file overrides system repository files/directories. no-network is supported, including the newer boolean-option parser. Native signed indexes and packages still use device trust keys; no allow-untrusted or force fallback is added.
- APK add can modify installed packages. The existing guard rejects native upgrade, downgrade, reinstall, replacement, purge and pinning changes before real installation.
- Native installed-size output agrees with the current package-space parser. Installation uses full-plan sizes and final command/capability probes.
- Login consent retains the original SSH connection binding, closed capture options stay closed, and fixed-required hcxdumptool remains checked. Cancellation and failed verification do not become success.
- curl is absent from the existing signed local bundle roots. Its missing batch requires router network or computer download; other locally covered groups keep their own route.

## Primary references

- [OpenWrt 21.02 package build](https://raw.githubusercontent.com/openwrt/openwrt/openwrt-21.02/package/system/opkg/Makefile)
- [OpenWrt 25.12 package build](https://raw.githubusercontent.com/openwrt/openwrt/openwrt-25.12/package/system/apk/Makefile)
- [Pinned opkg CLI](https://raw.githubusercontent.com/openwrt/opkg-lede/1bf042dd06751b693a8544d2317e5b969d666b69/src/opkg-cl.c)
- [Pinned opkg configuration loader](https://raw.githubusercontent.com/openwrt/opkg-lede/1bf042dd06751b693a8544d2317e5b969d666b69/libopkg/opkg_conf.c)
- [APK add semantics](https://raw.githubusercontent.com/alpinelinux/apk-tools/v3.0.5/doc/apk-add.8.scd)
- [APK source/repository and simulation options](https://raw.githubusercontent.com/alpinelinux/apk-tools/v3.0.5/doc/apk.8.scd)
- [Pinned APK native action output](https://raw.githubusercontent.com/alpinelinux/apk-tools/b5a31c0d865342ad80be10d68f1bb3d3ad9b0866/src/commit.c)

## Validation boundaries

56 focused offline tests passed in this review. These cover native output contracts, guards, dependency providers, network-body integrity, cancellation and the 9 login installation pipeline cases. The 9 pipeline cases retain real consent dispatch, service orchestration, dependency probes, signed repository validation, plan/space checks and post-install verification; only the human answer and SSH/SFTP peer are simulated.

Windows Python/Qt offline execution is verified. Debian 12 desktop runtime and installation on the feedback router remain unverified. No new application build or publication was performed.
