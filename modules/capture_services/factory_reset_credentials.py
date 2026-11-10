"""Forget only the authenticated reset target after a confirmed reset command."""
from PySide6.QtCore import QObject


class FactoryResetCredentials(QObject):
    def __init__(self, service):
        super().__init__(service)
        self.service = service
        self.targets = {}
        self.pending_deletions = set()
        service.completed.connect(self.completed)
        service.failed.connect(self.failed)

    @staticmethod
    def identity(settings):
        return (str(settings.get('host', '')).strip().casefold(), int(settings.get('port', 22)),
                str(settings.get('user', 'root')).strip().casefold())

    def is_pending(self, settings):
        return self.identity(settings) in self.pending_deletions

    def capture(self, key, action, arguments, ssh):
        if action != 'maintenance_apply' or arguments.get('review', {}).get('operation') != 'reset':
            return
        settings = getattr(ssh, 'authenticated_settings', None)
        if not isinstance(settings, dict) or not settings:
            settings = dict(host=getattr(ssh, '_host', ''), port=getattr(ssh, '_port', 22), user=getattr(ssh, '_user', 'root'))
        settings = {name: settings.get(name) for name in ('host', 'port', 'user')}
        if not settings['host'] or not settings['user']:
            return
        self.targets[key] = (settings, ssh._client)

    def failed(self, key, _message):
        self.targets.pop(key, None)

    def completed(self, key, result):
        target = self.targets.pop(key, None)
        if target is None or result.get('operation') != 'reset' or result.get('outcome') != 'reconnect':
            return
        settings, connection = target
        owner = self.service.owner
        ssh = self.service.manager.ssh
        # Do not wipe a newer session or credential if another login superseded it.
        current = (getattr(ssh, '_host', ''), getattr(ssh, '_port', 22), getattr(ssh, '_user', 'root'))
        identity = (settings['host'], settings['port'], settings['user'])
        if current == identity and ssh._client is not connection and ssh._client is not None:
            return
        pending = self.identity(settings)
        self.pending_deletions.add(pending)
        if current == identity and (ssh._client is connection or ssh._client is None):
            owner.set_openwrt_password('')
            self.service.passwordInvalidated.emit(settings)

        def finished(_value, error):
            self.pending_deletions.discard(pending)
            if error is not None:
                owner.settingsMessageGenerated.emit(
                    "\u6062\u590d\u51fa\u5382\u540e\u6e05\u9664\u672c\u673a\u65e7\u5bc6\u7801\u5931\u8d25\uff1a" + str(error), 'error')
        try:
            owner.credentials.submit('delete', owner._openwrt_credential_target(settings),
                                     settings['user'], '', self, finished)
        except OSError as error:
            finished(None, error)
