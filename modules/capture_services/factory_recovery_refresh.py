"""Refresh post-reset adapter choices off the GUI thread, never guess an interface."""
from threading import Event
from uuid import uuid4
import time
import ipaddress
from PySide6.QtCore import QObject


class FactoryRecoveryRefresh(QObject):
    def __init__(self, broker):
        super().__init__(broker)
        self.broker = broker
        self.jobs = {}

    def refresh(self, key, requested=None):
        broker = self.broker
        item = broker.offers.get(key)
        if broker.owner._shutting_down or key not in broker.standalone or item is None or key in self.jobs:
            return False
        gate = item[0]
        def notify():
            current = broker.offers.get(key)
            if current is not None and current[0] is gate:
                broker.owner.configurationProgressChanged.emit(key, dict(current[1], network_recovery=broker.payload(key)))

        rejected = ''
        with gate.lock:
            if gate.busy:
                return False
            if not gate.armed or gate.closed:
                rejected = "\u8bf7\u5148\u786e\u8ba4\u8def\u7531\u5668\u5df2\u91cd\u542f\u5e76\u8fde\u63a5\uff0c\u518d\u52fe\u9009\u786e\u8ba4\u3002"
            elif gate.deadline - time.monotonic() < 15:
                rejected = "\u672c\u6b21\u7b49\u5f85\u65f6\u95f4\u5df2\u5230\uff0c\u8bf7\u91cd\u65b0\u52fe\u9009\u201c\u6211\u5df2\u786e\u8ba4\u8def\u7531\u5668\u91cd\u542f\u5b8c\u6210\u201d\u540e\u91cd\u8bd5\u3002"
            if rejected:
                gate.message = rejected
            else:
                gate.busy = True
                gate.message = "\u6b63\u5728\u6838\u5bf9\u5f53\u524d\u7f51\u5361\u548c\u8fde\u63a5\u65b9\u5f0f\u2026"
                defaults = dict(gate.plan['factory_refresh_defaults'])
                previous_plan = dict(gate.plan)
        if rejected:
            notify()
            return False
        cancel = Event()
        self.jobs[key] = cancel

        def operation(event):
            from modules.platform_services.lan_network import inspect
            from .lan_client_recovery import factory_reset_plan
            if event.is_set() or not gate.permit():
                raise InterruptedError('Network recovery closed')
            inventory = inspect(defaults.get('address') or None, timeout=10, include_unaddressed=True)
            if inventory.get('error'):
                raise RuntimeError("\u8bfb\u53d6\u5f53\u524d\u7f51\u5361\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5\u3002")
            # A fresh OS route can select DHCP interfaces. It is not evidence for
            # carrying a saved static-IP migration onto a different interface.
            # A default/VPN route is not proof of a local connection to the reset router.
            try:
                destination = ipaddress.IPv4Address(defaults.get('address', ''))
            except (TypeError, ValueError):
                destination = None
            rows = []
            for adapter in inventory.get('adapters', []):
                on_link = False
                for address in adapter.get('addresses', []):
                    try:
                        local = ipaddress.IPv4Interface(address)
                        on_link = on_link or (destination is not None and destination in local.network and local.ip != destination)
                    except (TypeError, ValueError):
                        continue
                rows.append(dict(adapter, route=bool(adapter.get('route') and on_link)))
            inventory = dict(inventory, adapters=rows)
            plan = factory_reset_plan(inventory, defaults)
            migration = previous_plan.get('static_migration')
            if migration:
                old = migration.get('adapter', {})
                for index, fresh in enumerate(plan['adapters']):
                    if fresh.get('dhcp') is not False:
                        continue
                    if (fresh.get('id'), fresh.get('mac'), fresh.get('addresses')) == (old.get('id'), old.get('mac'), old.get('addresses')):
                        plan.update(static_migration=migration, new_addresses=previous_plan.get('new_addresses', []),
                                    old_addresses=previous_plan.get('old_addresses', []), guide=previous_plan['guide'])
                        plan['adapters'][index] = dict(fresh, static_recovery=True)
                        break
            plan['factory_refresh_defaults'] = defaults
            note = previous_plan.get('factory_result_note', '')
            plan['factory_result_note'] = note
            if note and not plan['guide'].startswith(note):
                plan['guide'] = note + '\n\n' + plan['guide']
            return plan

        def current():
            self.jobs.pop(key, None)
            item = broker.offers.get(key)
            if item is None or item[0] is not gate:
                return False
            with gate.lock:
                gate.busy = False
            if not gate.permit() or broker.owner._shutting_down:
                gate.message = "\u672c\u6b21\u7f51\u7edc\u6062\u590d\u7b49\u5f85\u5df2\u7ed3\u675f\uff0c\u8bf7\u91cd\u65b0\u786e\u8ba4\u8fde\u63a5\u540e\u91cd\u8bd5\u3002"
                notify()
                return False
            return True

        def completed(plan):
            if not current():
                return
            with gate.lock:
                gate.plan = plan
                gate.adapters = plan['adapters']
                gate.busy = False
                gate.attempted.clear()
                gate.message = ''
            identity = requested if any(row['id'] == requested for row in gate.adapters) else None
            if requested and identity is None and not plan.get('selection_required') and len(gate.adapters) == 1:
                identity = gate.adapters[0]['id']
            if requested and identity is None:
                gate.message = "\u8fde\u63a5\u65b9\u5f0f\u5df2\u53d8\u5316\uff0c\u8bf7\u9009\u62e9\u5f53\u524d\u7f51\u5361\u540e\u91cd\u8bd5\u3002"
            notify()
            if identity:
                broker.request(key, identity, inventory_checked=True)

        def failed(message, detail):
            if not current():
                return
            with gate.lock:
                gate.busy = False
                gate.message = str(message)
            notify()

        notify()
        from .task_completion import start_capture_task
        try:
            started = start_capture_task(broker.owner, 'network-inspect-' + uuid4().hex,
                                         operation, cancel, completed, failed)
            if started is False:
                failed('Network inspection could not start', '')
                return False
        except Exception as exc:
            failed(str(exc), '')
            return False
        broker.owner.activityChanged.emit()
        return True

    def release(self, key):
        cancel = self.jobs.get(key)
        if cancel is not None:
            cancel.set()
