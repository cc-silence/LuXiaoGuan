"""Secret Service storage used only inside the bounded credential worker."""
from contextlib import closing, contextmanager
from .credentials import CredentialError, validate_request

APPLICATION = "RouterManager"


@contextmanager
def _collection():
    try:
        import secretstorage
        from secretstorage.collection import get_collection_by_alias, SESSION_COLLECTION
        from secretstorage.util import open_session
    except ImportError:
        raise CredentialError("missing_dependency") from None
    try:
        with closing(secretstorage.dbus_init()) as connection:
            collection = get_collection_by_alias(connection, "default")
            if collection.collection_path == SESSION_COLLECTION:
                raise CredentialError("unavailable")
            if collection.is_locked():
                raise CredentialError("locked")
            session = open_session(connection)
            if not session.encrypted:
                raise CredentialError("unencrypted")
            collection.session = session
            yield collection
    except CredentialError:
        raise
    except Exception:
        # D-Bus exceptions can contain request data. Never expose their raw text.
        raise CredentialError("unavailable") from None


def _matches(collection, attributes):
    items = []
    for index, item in enumerate(collection.search_items(attributes)):
        if index >= 8:
            raise CredentialError("ambiguous")
        actual = item.get_attributes()
        if any(actual.get(key) != value for key, value in attributes.items()):
            raise CredentialError("verification")
        if item.is_locked():
            raise CredentialError("locked")
        items.append(item)
    return items


def perform(payload):
    payload = validate_request(payload)
    attributes = {"application": APPLICATION, "target": payload["target"]}
    operation = payload["operation"]
    with _collection() as collection:
        items = _matches(collection, attributes)
        if operation == "read":
            if len(items) > 1:
                raise CredentialError("ambiguous")
            if not items:
                return ""
            raw = items[0].get_secret()
            if not isinstance(raw, bytes) or len(raw) > 16384:
                raise CredentialError("verification")
            try:
                text = raw.decode("utf-8")
            except UnicodeError:
                raise CredentialError("verification") from None
            if len(text) > 4096:
                raise CredentialError("verification")
            return text
        if operation == "write":
            if len(items) > 1:
                raise CredentialError("ambiguous")
            secret = payload["password"].encode("utf-8")
            collection.create_item("RouterManager router password", attributes, secret,
                                   replace=True, content_type="text/plain; charset=utf-8")
            verified = _matches(collection, attributes)
            if len(verified) != 1 or verified[0].get_secret() != secret:
                raise CredentialError("verification")
        else:
            for item in items:
                item.delete()
            if _matches(collection, attributes):
                raise CredentialError("verification")
    return True
