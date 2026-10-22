"""Explicit empty-password authentication after SSH host-key verification."""
from paramiko import AuthenticationException, BadAuthenticationType
from paramiko.auth_strategy import AuthStrategy


class EmptyPasswordAuthStrategy(AuthStrategy):
    """Try none once, then one empty-password attempt if the server permits it."""
    def __init__(self, username):
        super().__init__(ssh_config=None)
        self.username=username

    def authenticate(self, transport):
        allowed=None
        try:
            allowed=transport.auth_none(self.username)
        except BadAuthenticationType as exc:
            allowed=exc.allowed_types
        except AuthenticationException as exc:
            # Do not retry timeouts or a transport failure as a different method.
            if str(exc)!="Authentication failed.":raise
        if transport.is_authenticated():return
        if allowed is not None and not {"password","keyboard-interactive"}.intersection(allowed):
            raise BadAuthenticationType("Empty password authentication is not permitted",allowed)
        remaining=transport.auth_password(self.username,"")
        if not transport.is_authenticated():
            if remaining:
                raise BadAuthenticationType("Additional authentication is required",remaining)
            raise AuthenticationException("Authentication failed.")
