"""Prepare one interface per PHY for the hcx 6.3.4 enumerator."""
import re
import secrets
from .command_runner import require_command_completion


def placeholder_removal_command(name, phy):
    if (not re.fullmatch(r"hcxstub[0-9]+", name) or not re.fullmatch(r"phy[0-9]+", phy)
            or name[7:] != phy[3:]):
        raise ValueError("Invalid capture placeholder identity")
    path = "/sys/class/net/" + name
    return (f'if [ -e {path} ]; then '
            f'[ "$(cat {path}/ifalias)" = routermanager:autocapture-stub ] || exit 74; '
            f'case "$(readlink {path}/phy80211)" in */{phy}) :;; *) exit 74;; esac; '
            f'iw dev {name} del || exit 75; fi; test ! -e {path}')


def release_owned_placeholder(session):
    if not re.fullmatch(r"phy[0-9]+", session.phy):
        raise ValueError("Invalid physical interface")
    name = "hcxstub" + session.phy[3:]
    sessions = getattr(session.service, "sessions", {})
    if not isinstance(sessions, dict):
        return
    owners = [owner for owner in sessions.values()
              if name in owner._created_stubs
              and owner._bound_client is session._bound_client
              and owner._bound_fingerprint == session._bound_fingerprint]
    if len(owners) > 1:
        raise RuntimeError("Ambiguous capture placeholder ownership")
    if not owners:
        return
    command = placeholder_removal_command(name, session.phy)
    result = session.runner.run("replace_owned_phy_stub", command, timeout=6,
                                cancel_event=session._stop_event)
    require_command_completion(result)
    if result.exit_code != 0:
        raise RuntimeError("Could not verify removal of owned capture placeholder")
    owners[0]._created_stubs.remove(name)


def build_placeholder_script(exclude_phy=""):
    # Called only after all requested real monitor interfaces are ready.
    prefix = "02:" + ":".join(f"{value:02x}" for value in secrets.token_bytes(3))
    exclusion = f'  [ "$_phy" != "{exclude_phy}" ] || continue; ' if re.fullmatch(r"phy[0-9]+", exclude_phy) else ""
    stub_script = (
        "_inventory=$(iw dev 2>/dev/null) || exit 76; "
        "for _p in /sys/class/ieee80211/*; do "
        "  [ -d \"$_p\" ] || continue; "
        "  _phy=$(basename \"$_p\"); "
        + exclusion +
        "  _num=\"${_phy#phy}\"; "
        "  if ! printf \"%s\\n\" \"$_inventory\" | grep -q \"^phy#${_num}$\"; then "
        "    _sname=\"hcxstub${_num}\"; "
        "    case \"$_num\" in ''|*[!0-9]*) echo STUB_FAILED; continue;; esac; "
        "    [ \"$_num\" -le 65535 ] || { echo STUB_FAILED; continue; }; "
        f"    _rand_mac=$(printf '{prefix}:%02x:%02x' \"$((_num / 256))\" \"$((_num % 256))\"); "
        "    if iw phy \"$_phy\" interface add \"$_sname\" type monitor addr \"$_rand_mac\" 2>/dev/null; then "
        "      echo \"STUB_CREATED:$_sname\"; "
        "      if ! { printf %s routermanager:autocapture-stub > /sys/class/net/\"$_sname\"/ifalias && "
        "             ip link set \"$_sname\" up 2>/dev/null; }; then echo STUB_FAILED; fi; "
        "    else echo STUB_FAILED; fi; "
        "  fi; "
        "done"
    )
    return stub_script


def prepare_placeholders(self, *, exclude_phy=""):
    stub_script = build_placeholder_script(exclude_phy)
    stub_res = self.runner.run("ensure_phy_stubs", stub_script, timeout=8, cancel_event=self._stop_event)
    if stub_res.stdout:
        for line in stub_res.stdout.splitlines():
            if line.startswith("STUB_CREATED:"):
                stub_name = line.split(":", 1)[1].strip()
                if re.fullmatch(r"hcxstub[0-9]+", stub_name) and stub_name not in self._created_stubs:
                    self._created_stubs.append(stub_name)

    require_command_completion(stub_res)
    if stub_res.exit_code != 0 or "STUB_FAILED" in (stub_res.stdout or ""):
        raise RuntimeError("Capture placeholder preparation failed; owned resources will be cleaned up.")
