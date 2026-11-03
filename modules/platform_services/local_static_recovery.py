"""Explicit PC-only repair using the shared static migration transaction."""
import copy
import time
from .static_network import StaticMigration,StaticMigrationError


def recover(plan,cancel,permit,*,timeout=60):
    deadline=time.monotonic()+timeout
    permitted=lambda:not cancel.is_set() and time.monotonic()<deadline and permit()
    if not permitted():raise InterruptedError('Network recovery cancelled')
    transaction=StaticMigration(plan)
    transaction.deadline=deadline
    try:
        transaction.prepare(cancel)
        if not permitted():raise InterruptedError('Network recovery cancelled')
        transaction.activate()
        if not permitted():raise InterruptedError('Network recovery cancelled')
        transaction.commit_local()
        row=copy.deepcopy(plan['adapter'])
        row.update(transaction.expected)
        row.pop('static_recovery',None)
        row['local_settings_updated']=True
        return row
    except Exception:
        try:transaction.rollback()
        except Exception as recovery:
            raise StaticMigrationError('Local network restoration remains unresolved; recovery record retained') from recovery
        raise
