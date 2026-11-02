"""Read-only virtualization hints. Never infer a host bridge or NAT mode."""
from pathlib import Path


def hints(text):
    value=str(text).casefold()
    vmware='vmware' in value
    observed=vmware or any(token in value for token in ('virtualbox','qemu','kvm','virtual machine','xen','parallels'))
    return dict(virtual_machine_observed=True if observed else None,
                vmware_observed=True if vmware else None,
                host_network_config_visible=False if observed else None,
                virtual_network_mode_known=False)


def windows():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'HARDWARE\DESCRIPTION\System\BIOS') as key:
            values=[]
            for name in ('SystemManufacturer','SystemProductName','BIOSVendor'):
                try:values.append(str(winreg.QueryValueEx(key,name)[0])[:256])
                except OSError:pass
        return hints(' '.join(values))
    except OSError:return hints('')


def linux():
    values=[]
    for name in ('sys_vendor','product_name','bios_vendor'):
        try:
            with (Path('/sys/class/dmi/id')/name).open('rb') as stream:
                values.append(stream.read(256).decode('utf-8',errors='replace'))
        except OSError:pass
    return hints(' '.join(values))
