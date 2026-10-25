# Common-tool removal audit (2026-10-25)

The wpa_cli and signal entries now allow confirmed removal. The signal entry submits four separate package names, not its comma-separated display label. Shared CommonTool definitions drive both UI and service permissions.

Removal inventory uses the actual package manager selected by the existing environment probe. Runtime availability alone never selects a package or a provider. Only exact declared package names that are installed are eligible. A wpa_cli command supplied by another package does not authorize removal of wpad, wpa-supplicant or another provider. There is no reliable historical ownership ledger identifying which application installed an existing package; no such ownership claim is made.

Each removal uses the existing native dry-run and plan verifier, then the real native command. No force, recursive reverse-dependency removal, autoremove, manual file deletion or authentication-service replacement is introduced. APK automatically removes orphan dependencies: if its dry-run proposes deletion outside the single requested package, existing verification rejects it. This conservative behavior is deliberately retained, rather than pretending that omitting --autoremove disables APK cleanup.

Signal components are handled in reverse dependency order: luci-lib-jsonc, libubus-lua, libuci-lua, lua. Native dependency refusal stops the task; previously completed removals are not rolled back. The confirmation explicitly states this. Shared components required by declared dependencies are preserved. Undeclared consumers and user scripts cannot be inferred from package metadata; removing any dependency can disable this application's corresponding features until reinstalled.

After successful common-tool removal, the installed-package inventory is read again and remaining requested packages cause an error. Service completion does not misinterpret an alternative executable provider as a failed exact-package uninstall. Runtime status is still refreshed for display. Missing exact packages are skipped with an explanation, never replaced with provider package names.

## Compatibility scope and evidence

- Official OpenWrt 19.07, 21.02, 22.03, 23.05 and 24.10 use the existing opkg path: list-installed, --noaction remove, remove. No version-specific new flags are added.
- Official OpenWrt 25.12 uses the existing APK path: info, del --simulate, del. Actual probe selection, not release strings, chooses the implementation.
- https://openwrt.org/docs/guide-user/additional-software/opkg documents list-installed, remove, --noaction and the force/autoremove options that are not used here.
- https://lxr.openwrt.org/source/opkg-lede/libopkg/opkg_cmd.c documents native opkg's package-by-package removal behavior. Libraries precede their Lua interpreter; the application does not force group removal.
- https://openwrt.org/docs/guide-user/additional-software/opkg-to-apk-cheatsheet documents APK del --simulate and Purging output.
- https://github.com/openwrt/luci/blob/master/applications/luci-app-package-manager/root/usr/libexec/package-manager-call explicitly documents APK's default orphan dependency cleanup.
- The change operates on userspace package metadata. It introduces no CPU architecture, driver, radio mapping, kernel module or hardware-specific command. This reasoning applies across the supported hardware inventory; it is not evidence of live verification on every listed model.

## Validation

Targeted tests cover both native listing formats, exact package selection, alternative providers, bounded/truncated/invalid inventory, failed commands, timeout/cancellation, native shared-dependency refusal, APK extra cleanup, signal removal order, unknown packages and post-removal inventory. The real source window verifies both buttons, accepted/cancelled confirmation and the submitted package list. Actual renders cover dark/light and 100/125/150/175 percent scaling. Python syntax, strict UTF-8 and diff checks are also required.

No live router packages were removed during this audit. Offline tests do not prove package metadata, undeclared plugin dependencies or execution behavior on a particular router.
