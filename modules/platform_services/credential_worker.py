"""Credential subprocess protocol: secrets travel on private stdin/stdout pipes only."""
import json
import sys
from .credentials import CredentialError, MAX_BYTES, validate_request
from .runtime import is_linux


def run_worker(argv, input_stream=None, output_stream=None):
    incoming = sys.stdin.buffer if input_stream is None else input_stream
    outgoing = sys.stdout if output_stream is None else output_stream
    payload = None
    try:
        if len(argv) != 2 or argv[1] != "--credential-worker" or not is_linux():
            raise CredentialError("unsupported")
        raw = incoming.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise CredentialError("invalid")
        try:
            payload = validate_request(json.loads(raw))
        except (ValueError, TypeError, UnicodeError):
            raise CredentialError("invalid") from None
        from .linux_credentials import perform
        value = perform(payload)
        reply = {"ok": True, "value": value}
        code = 0
    except CredentialError as exc:
        reply, code = {"ok": False, "code": exc.code}, 1
    except Exception:
        reply, code = {"ok": False, "code": "unavailable"}, 1
    finally:
        if isinstance(payload, dict):
            payload.clear()
    text = json.dumps(reply, ensure_ascii=True)
    if len(text.encode("utf-8")) > MAX_BYTES:
        text, code = '{"ok":false,"code":"verification"}', 1
    outgoing.write(text + "\n")
    outgoing.flush()
    return code
