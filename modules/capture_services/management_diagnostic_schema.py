"""Finite non-secret protocol labels shared by capture and export."""
ACTIONS=frozenset({"policy_status","policy_read","policy_preview","policy_apply","common_read","common_save",
    "maintenance_preview","maintenance_backup","maintenance_apply","maintenance_reboot","password_change",
    "move_preview","relocate","orphan_preview","orphan_cleanup","policy_validation","common_validation","maintenance_validation"})
KINDS=frozenset({"initial","station","ap","boot","network","system","restore","reset","backup","reboot","relocation","orphan","status","read","password"})
CODES=frozenset({"pending","changed","official","common_values","common_section","common_legacy","common_protocol",
    "read","response","transport","timeout","disconnected","dependencies","cancelled","save_commit","save_backup",
    "save_recovery","save_unsupported","save_large","files","file_recovery","session","section","values",
    "identity","identity_shared_settings","identity_duplicate","identity_changed","identity_backup","identity_network",
    "orphan_runtime","orphan_selection","orphan_large","move_system","move_management","move_dhcp","pending_commit",
    "pending_recovery","internal","network","radio","dhcp_changed","recovery"})

SITE_MODULES=frozenset({"router_policy_plan","upstream_firewall_zones","common_settings","router_backup",
    "backup_archive","router_password","router_reboot","router_policy","orphan_interface_manager",
    "relocation_manager","wireless_setup_manager","command_runner","signal_manager","router_management_service",
    "common_config_actions","router_policy_actions","maintenance_actions","wireless_setup_service",
    "router_config_dialog","ap_batch_dialog"})

# Exact native error codes from the management Lua protocols.
CODES |= frozenset({
    'action','bridge_owner','changed','common_legacy','common_section',
    'common_values','dhcp_changed','identity','identity_backup','identity_changed',
    'identity_duplicate','identity_network','identity_shared_settings','large','move_management',
    'move_shared','move_system','network','official','orphan_large',
    'orphan_runtime','orphan_selection','pending','read','response',
    'rpc_network_interface_dump','rpc_session_create','rpc_session_destroy','rpc_uci_add','rpc_uci_delete',
    'rpc_uci_order','rpc_uci_set','save_backup','save_commit','save_large',
    'save_recovery','save_unsupported','section','session','shared',
    'values',
})

CODES |= frozenset({"common_save_uncertain","policy_commit_uncertain","unconfirmed"})
