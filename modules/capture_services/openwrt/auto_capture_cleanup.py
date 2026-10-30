"""Verified monitor removal shared by preflight and final capture cleanup."""
import re
from .command_builder import quote_argument as q
from .command_runner import require_command_completion

def monitor_removal_command(interface, phy):
    if not re.fullmatch(r"hcxmon[0-9]+",interface) or not re.fullmatch(r"phy[0-9]+",phy):
        raise RuntimeError("Cannot verify monitor interface ownership")
    path=q('/sys/class/net/'+interface)
    return (
        'if [ ! -e '+path+' ]; then printf ABSENT; exit 0; fi; '
        'info=$(iw dev '+q(interface)+' info) || exit 41; '
        'printf "%s\\n" "$info" | grep -Eq "^[[:space:]]*type monitor[[:space:]]*$" || exit 42; '
        'printf "%s\\n" "$info" | grep -Eq "^[[:space:]]*wiphy '+phy[3:]+'[[:space:]]*$" || exit 43; '
        'iw dev '+q(interface)+' del || exit 44; '
        '[ ! -e '+path+' ] || exit 45; printf REMOVED'
    )

def verified_monitor_result(result):
    require_command_completion(result)
    if result.exit_code != 0 or result.stdout.strip() not in {'ABSENT','REMOVED'}:
        stages={41:'read_interface',42:'interface_type',43:'interface_phy',44:'delete_interface',45:'verify_absent'}
        detail=str(result.stderr or result.stdout or 'no diagnostic output')[:2048]
        raise RuntimeError('Monitor removal could not be verified: stage='+
                           stages.get(result.exit_code,'response')+
                           ' exit='+str(result.exit_code)+'; '+detail)
    return result.stdout.strip()

def remove_monitor(runner, interface, phy):
    result=runner.run('del_mon_iface',monitor_removal_command(interface,phy),timeout=8)
    return verified_monitor_result(result)
