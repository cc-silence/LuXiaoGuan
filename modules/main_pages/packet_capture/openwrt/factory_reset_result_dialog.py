"""Factory-reset guidance with explicit confirmation that the device has restarted."""
from modules.capture_services.lan_client_recovery import factory_reset_plan
from .network_recovery_result_dialog import NetworkRecoveryResultDialog


class FactoryResetResultDialog(NetworkRecoveryResultDialog):
    def __init__(self,result,inventory,owner,parent=None):
        plan = factory_reset_plan(inventory, result.get('factory_login', {}))
        plan['factory_refresh_defaults'] = dict(result.get('factory_login', {}))
        plan['factory_result_note'] = result['note']
        super().__init__(result,plan,owner,parent,
                         requires_restart_confirmation=True)
