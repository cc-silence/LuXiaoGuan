"""Factory-reset guidance with explicit confirmation that the device has restarted."""
from modules.capture_services.lan_client_recovery import factory_reset_plan
from .network_recovery_result_dialog import NetworkRecoveryResultDialog


class FactoryResetResultDialog(NetworkRecoveryResultDialog):
    def __init__(self,result,inventory,owner,parent=None):
        super().__init__(result,factory_reset_plan(inventory),owner,parent,
                         requires_restart_confirmation=True)
