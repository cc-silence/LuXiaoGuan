# Release delivery files

The dist root contains application ZIPs and the offline package directories.
Application publication descriptors and validation reports are stored under
build/release-metadata/<archive-stem>/. The publisher resolves that location
first and accepts the old adjacent descriptor location for older releases.

scripts/build_release.py runs the shared metadata cleanup after successful
packaging. It preserves ZIPs and unrelated files. Existing records are retained;
a conflicting record prevents cleanup instead of overwriting either copy.

Offline support information is maintained in docs/offline-packages/support.json
and support.txt. Delivery and publication update these source records without
creating duplicate support files in the dist root. Legacy mirrors are removed
only when their bytes match the source; differing copies are archived instead.
Offline bundle validation reports remain beside their ZIPs inside the respective
offline package directories because those reports are used to verify publication.

## Full-package automatic updates

The application downloads the entire signed ZIP in the background, verifies its
size and SHA-256, safely extracts managed program files, and derives an install
inventory locally. An independent Windows helper re-verifies the archive before
transactional backup/replacement, startup validation, rollback on failure and
restart. User configuration and custom files remain protected. Active capture,
terminal and file-transfer tasks block installation rather than being killed.

Publications upload only a complete ZIP and small signed version metadata.
No blobs/catalogs are generated or uploaded. The local baseline and installation
inventory are recovery data, not separately hosted update files. Downloads can
be cancelled/resumed and obsolete verified stages are reclaimed in the background.

Retire old cloud content with `scripts/manage_publishing.py retire-incremental`
(preview), then `--apply`. The migration verifies all channels, backs up signed
metadata, downloads and verifies current complete packages, and switches every
application channel to manual delivery before deleting any incremental object.
Deletion uses immutable ETags, bounded workers, per-batch channel rechecks and a
persistent audit record. Unknown content, signature failure, unavailable packages
or competing channel changes stop deletion. All current application ZIPs and
offline packages remain protected. The caller may additionally validate metadata
with actual shipped clients before any channel write.

## Mandatory build integrity

Application builds use PyInstaller --clean. The application must pass frozen project-code comparison against current source before delivery. The independent installer and local baseline are included for backup/recovery, without cloud incremental content. A stale cached module fails the build. Full per-module SHA-256 evidence remains in the validation report; console summaries omit the large maps.

Application and offline publication use the project-isolated publisher identity and a 1,000,000,000 byte peak storage limit. Before upload, count all bucket objects and incoming immutable content. Only verified, unreferenced history can be removed for headroom. Current formal releases are never deleted before replacement verification. Only when ordinary reclamation cannot fit an alpha upload may that same platform's current alpha ZIP be retired first. A durable signed preparation and a confirmed publishing/503 maintenance state must precede retirement; retries resume those exact verified inputs. Formal releases and the other platform remain protected. If retiring this alpha still cannot fit the projected peak, stop before mutation. Unknown channels, changing references or insufficient safe headroom stop publication. Cleanup and upload share a local publisher lock.

After successful public download/signature verification and channel switching, retain exactly one current application ZIP in each of four slots: Windows stable, Windows alpha, Linux stable and Linux alpha; retain only the current offline ZIP and immutable manifest per dependency kind (common/capture). Automatically remove superseded archives and their verified offline manifests even below quota. New publications do not create update blobs/catalogs. Retired incremental content is reclaimed only through the audited manual-delivery migration above. Public package filename contracts must remain compatible with shipped clients: LuXiaoGuan.exe is stable; the Chinese display name is independent. Validate each publication against actual earlier client code, including executable and retired-path allowlists.


## Publisher transport

Publish with scripts/manage_publishing.py. Its optional Python transport dependency is installed only into infra/update_gateway/python_runtime using the pinned requirements-publisher.txt (pip --target); never install it into the application or global interpreter. Uploads stream bounded blocks with a 900-second total deadline and use the same isolated Cloudflare identity. Immutable-object network failures may retry the next configured HTTP/SOCKS proxy, without direct fallback. Authentication failures and uncertain channel writes stop for reconciliation rather than retrying blindly.

Local staging is bounded independently of online retention. A successful application
build keeps only its latest verified build directory; publication keeps only one
local compressed-object cache. Offline delivery removes reproducible staging
copies after the delivered ZIP passes its hash and CRC checks. The common owner
is scripts/build_cleanup.py, which previews by default and applies only with
--apply. Signed records, publisher credentials and dist delivery files are kept.

Standalone old-package cleanup: python scripts/manage_publishing.py cleanup-releases previews; add --apply to delete only verified superseded packages. There is no extra confirmation when the user has already authorized cleanup.


## One-time legacy compatibility bridge

The owner explicitly authorized one transition release for old incremental-only
clients. `publish-app --legacy-bridge` generates a single legacy catalog/object
set while shipping the new complete-ZIP updater. The publisher preserves a signed
compatibility channel and its package. Ordinary later publications remain ZIP-only.
The gateway serves the bridge to old clients and the current regular channel to
clients sending `X-RouterManager-Update-Mode: full-package`. Testing-code and
platform restrictions remain enforced. Keep this bridge until the owner confirms
legacy migration is finished; normal retention and incremental cleanup must not
delete it. It temporarily adds one protected package to the regular four slots.


Bundled capture scripts and hcx packages are staged under bin/tools, which is
inside the managed program roots accepted by already shipped full-ZIP clients.
Source checkouts continue to use tools. All consumers resolve this through the
shared bundled_tools helper. Never copy downloaded offline_packages caches into
an application ZIP. Verify the complete new archive with shipped client code,
not only its version notification, before publication.
