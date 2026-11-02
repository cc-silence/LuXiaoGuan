# LAN recovery and shared firewall zone evidence

Read-only additions to the existing bounded operation diagnostics:
- Observed virtual-machine and VMware firmware hints (unknown is not physical).
- Explicitly unknown bridge/NAT mode and unavailable host configuration inside an observed guest.
- Adapter medium, DHCP-server presence and relationships to client subnet and target router.
- Lease expiration when available from Windows interface registry metadata.
- Address, gateway, DNS and DHCP-server changes relative to the recovery snapshot.
- Targeted Windows reads retain addressless interfaces after DHCP release.
- Initial-policy shared-zone failures retain a specific code and finite membership flags/counts.

No raw system manufacturer, IP, MAC, custom zone name, password or driver text is added to feedback.
No host VMware settings are queried or modified. Firmware strings are hints, not proof of VM network mode.
Linux does not fabricate DHCP-server/lease data when the existing inventory lacks it.
Existing bounded queues, change-only readiness emission and task ownership are preserved.

Validation: targeted unit tests, anonymized export assertions and a read-only Windows inventory.
Debian 12 hardware/DMI and real DHCP renewal through the affected user's hypervisor remain unverified.
The supplied 16:31 feedback proves a preview-only shared-zone guard, not the actual zone members.
