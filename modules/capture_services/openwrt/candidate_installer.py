"""Try authenticated profiles, retrying only before package installation."""
from .candidate_policy import CandidatePlanRejected, requires_candidate
from .snapshot_bundle import candidate_profiles


def install_local_candidates(installer, wanted, device, profile, repository, directory, cancel, tell):
    trial = requires_candidate(device)
    profiles = candidate_profiles(repository, device) if trial else (profile,)
    last = None
    for candidate in profiles:
        try:
            return installer.install_local(wanted, candidate, repository, directory, cancel,
                                           candidate_trial=trial)
        except CandidatePlanRejected as exc:
            last = exc
            tell(str(exc), "info")
    raise last or ValueError("No compatible APK candidate")


def install_ipk_candidates(manager, wanted, device, repository, directory, installed,
                           compare, cache_dir, sftp_available, cancel, tell, uploader):
    from .snapshot_bundle import select_profile, SnapshotBundleMismatch
    last = None
    for candidate in candidate_profiles(repository, device):
        try:
            select_profile(repository, device, wanted, installed, compare, cancel, tell,
                           profiles=(candidate,))
        except SnapshotBundleMismatch as exc:
            last = exc
            continue
        selected = repository.ipk_selection(candidate, wanted, installed, compare)
        rows = {row["name"]: row for row in candidate["packages"]}
        paths = tuple(repository.materialize(rows[p.name], directory, cancel) for p in selected)
        try:
            if paths:
                manager._install_offline(wanted, device, cache_dir, sftp_available, cancel, tell,
                    uploader, local_paths=paths, local_packages={p.name:p.version for p in selected},
                    candidate_trial=True)
            return
        except CandidatePlanRejected as exc:
            last = exc
            tell(str(exc), "info")
    raise CandidatePlanRejected(last or ValueError("No compatible IPK candidate"))
