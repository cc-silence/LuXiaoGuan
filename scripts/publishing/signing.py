"""Independent signing identity stored in Windows Credential Manager."""
import base64
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from modules.platform_services.windows_credentials import read_generic_password, write_generic_password
from modules.app_update_services.manifest import canonical

TARGET = "RouterManager:Publisher:Ed25519:v1"


def signing_key(create=False):
    encoded = read_generic_password(TARGET)
    if not encoded:
        if not create:
            raise ValueError("RouterManager publisher signing key is not initialized")
        key = Ed25519PrivateKey.generate()
        encoded = base64.b64encode(key.private_bytes(serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode("ascii")
        write_generic_password(TARGET, "RouterManager Publisher", encoded)
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(encoded, validate=True))


def public_key(key):
    return base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode("ascii")


def sign(payload):
    from modules.app_update_services.trust import PUBLIC_KEY
    key = signing_key()
    if public_key(key) != PUBLIC_KEY:
        raise ValueError("RouterManager signing key does not match the client trust root")
    return canonical(dict(payload=payload,
        signature=base64.b64encode(key.sign(canonical(payload))).decode("ascii")))
