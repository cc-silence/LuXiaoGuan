"""Read-only pixiewps execution check, preserving loader and transport failures."""
import re


def read_version(runner, cancel_event=None):
    result = runner.run('dependency_pixiewps_version', 'pixiewps --version 2>&1',
                        timeout=10, cancel_event=cancel_event)
    if result.cancelled:
        raise RuntimeError('pixiewps version check cancelled')
    if result.timed_out:
        raise RuntimeError('pixiewps version check timed out')
    output = (result.stdout or '') + '\n' + (result.stderr or '')
    match = re.search(r'\bpixiewps\s+v?(\d+\.\d+(?:\.\d+)?)', output, re.I)
    # Upstream v1.4.2 returns ARG_ERROR (3) after a successful --version.
    if result.exit_code not in (0, 3) or not match:
        raise RuntimeError('pixiewps version check failed: ' + output.strip()[:300])
    return match.group(1)
