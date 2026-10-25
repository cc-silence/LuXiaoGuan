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

## Application update cache lifecycle

New update downloads use `.routermanager-update` inside the installation.
The old `.winbox-update` name is accepted only for recovery and cleanup. Active
transactions are recovered in their original location, without renaming files
under a running updater.

Startup maintenance runs once in the background, including offline mode, and
participates in application shutdown. Download and installation use the same
nonblocking cache lease as cleanup. Installation holds that lease through
restart and result recording. There is no periodic cleanup timer.

Completed or rolled-back stages discard downloaded objects, backups and helper
executables. Only two small authenticated history records are retained in the
new cache; completed legacy stages are retired completely. Signed downloads
superseded by the installed or newly selected version are also reclaimed. The
selected download, newer pending downloads, active recovery data, linked paths,
unknown files and unverified stages are preserved. A locked helper is retried on
the next startup or update attempt. User configuration and offline dependency
packages are outside this cleanup policy.

## Mandatory build integrity

Application builds use PyInstaller --clean. Both the application and update worker must pass frozen project-code comparison against current source before delivery. A stale cached module fails the build. Full per-module SHA-256 evidence remains in the validation report; console summaries omit the large maps.

Application and offline publication use the project-isolated publisher identity and a 1,000,000,000 byte peak storage limit. Before upload, count all bucket objects and incoming immutable content. Only verified, unreferenced history can be removed for headroom. Current formal releases are never deleted before replacement verification. Only when ordinary reclamation cannot fit an alpha upload may that same platform's current alpha ZIP be retired first. A durable signed preparation and a confirmed publishing/503 maintenance state must precede retirement; retries resume those exact verified inputs. Formal releases and the other platform remain protected. If retiring this alpha still cannot fit the projected peak, stop before mutation. Unknown channels, changing references or insufficient safe headroom stop publication. Cleanup and upload share a local publisher lock.

After successful public download/signature verification and channel switching, retain exactly one current application ZIP in each of four slots: Windows stable, Windows alpha, Linux stable and Linux alpha; retain only the current offline ZIP and immutable manifest per dependency kind (common/capture). Automatically remove superseded archives and their verified offline manifests even below quota. Shared update blobs/catalogs are preserved unless a separate complete reference audit proves them unused. Public package filename contracts must remain compatible with shipped clients: LuXiaoGuan.exe is stable; the Chinese display name is independent. Validate each publication against actual earlier client code, including executable and retired-path allowlists.


## Publisher transport

Publish with scripts/manage_publishing.py. Its optional Python transport dependency is installed only into infra/update_gateway/python_runtime using the pinned requirements-publisher.txt (pip --target); never install it into the application or global interpreter. Uploads stream bounded blocks with a 900-second total deadline and use the same isolated Cloudflare identity. Immutable-object network failures may retry the next configured HTTP/SOCKS proxy, without direct fallback. Authentication failures and uncertain channel writes stop for reconciliation rather than retrying blindly.

Local staging is bounded independently of online retention. A successful application
build keeps only its latest verified build directory; publication keeps only one
local compressed-object cache. Offline delivery removes reproducible staging
copies after the delivered ZIP passes its hash and CRC checks. The common owner
is scripts/build_cleanup.py, which previews by default and applies only with
--apply. Signed records, publisher credentials and dist delivery files are kept.

Standalone old-package cleanup: python scripts/manage_publishing.py cleanup-releases previews; add --apply to delete only verified superseded packages. There is no extra confirmation when the user has already authorized cleanup.
