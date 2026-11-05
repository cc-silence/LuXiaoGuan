"""Bounded Linux build subprocess groups; no shell interpolation or detached compilers."""
import os
import signal
import subprocess


def _stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait(timeout=3)
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    # The CMake parent may have exited while compiler children still run.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=3)


def run_build(command, stage, name, timeout, env=None):
    with (stage / (name + ".log")).open("wb") as log:
        process = subprocess.Popen(command, cwd=stage, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            _stop_group(process)
            raise
    if code:
        _stop_group(process)
        raise RuntimeError("Build phase " + name + " failed; see " + str(stage / (name + ".log")))
