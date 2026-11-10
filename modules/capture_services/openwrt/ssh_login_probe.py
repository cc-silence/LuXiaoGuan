"""Observe the real login transport, without a second connection or port scan."""

class PrivateKeyLoadError(RuntimeError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def load_selected_private_key(path):
    """Use Paramiko's public parser once; preserve associated OpenSSH certificates."""
    from pathlib import Path
    from cryptography.exceptions import UnsupportedAlgorithm
    import paramiko
    try:
        name = str(path)
        key_path = Path(name[:-9] if name.endswith("-cert.pub") else name).expanduser()
        cert_path = Path(str(key_path) + "-cert.pub")
        if key_path.stat().st_size > 1024 * 1024 or (cert_path.exists() and cert_path.stat().st_size > 1024 * 1024):
            raise ValueError("Private key or certificate exceeds supported size")
        return paramiko.PKey.from_path(path)
    except OSError as exc:
        raise PrivateKeyLoadError("key_file_unavailable", "无法读取选中的 SSH 私钥文件，请检查文件是否存在且可读。尚未发送登录认证请求。") from exc
    except (TypeError, paramiko.PasswordRequiredException) as exc:
        raise PrivateKeyLoadError("key_passphrase_required", "选中的 SSH 私钥需要解锁密码；当前仅支持未加密的私钥。尚未发送登录认证请求。") from exc
    except (ValueError, paramiko.SSHException, paramiko.pkey.UnknownKeyType, UnsupportedAlgorithm) as exc:
        raise PrivateKeyLoadError("key_file_invalid", "选中的 SSH 私钥格式无效或不受支持。请选择有效的 OpenSSH/PEM 私钥；尚未发送登录认证请求。") from exc


def observed_transport_factory(journal):
    def create(sock, **kwargs):
        import paramiko
        journal.update(stage="ssh_banner", tcp_connected=True)
        return paramiko.Transport(sock, **kwargs)
    return create


def negotiation_reason(error):
    import paramiko
    if isinstance(error, paramiko.AuthenticationException) and str(error) in {
            "Unable to agree on a pubkey algorithm for signing a 'ssh-rsa' key!",
            "Unable to agree on a pubkey algorithm for signing a 'ssh-rsa-cert-v01@openssh.com' key!"}:
        return "public_key_algorithm_mismatch"
    if isinstance(error, paramiko.ssh_exception.IncompatiblePeer):
        return "protocol_version_mismatch" if str(error).startswith("Incompatible version") else "algorithm_negotiation_failed"
    return None


def connection_message(error, evidence, port):
    import socket
    import paramiko
    reason = negotiation_reason(error)
    if reason == "public_key_algorithm_mismatch":
        return "SSH 已连通，但当前私钥与路由器没有共同支持的公钥签名算法，本次私钥登录未完成。请导出日志排查 SSH 兼容性；这不是已确认的密码错误。"
    if reason == "algorithm_negotiation_failed":
        return "已连通 SSH 服务，但安全算法协商不兼容（主机密钥、密钥交换或加密算法无共同选项）。本次登录未完成，无法据此判断密码是否正确；请导出日志排查兼容性。"
    if reason == "protocol_version_mismatch":
        return "目标服务回应了 SSH，但 SSH 协议版本不兼容。本次登录未完成，无法据此判断密码是否正确；请检查路由器 SSH 服务版本。"
    if isinstance(error, (paramiko.AuthenticationException, paramiko.PasswordRequiredException)):
        return ""
    if isinstance(error, FileNotFoundError):
        return ""
    if isinstance(error, TimeoutError) and evidence.get("stage") == "authentication":
        return "已进入 SSH 认证，但等待登录结果超时。目前不能确定密码是否正确，请稍后重试。"
    if evidence.get("stage") == "authentication" and isinstance(error, (ConnectionError, EOFError, paramiko.SSHException)):
        return "已进入 SSH 认证，但认证期间连接中断或被重置，本次登录未完成。目前无法据此判断密码是否正确，请检查连接后重试。"
    if evidence.get("stage") in {"host_key", "authentication", "configuration"}:
        return ""
    if isinstance(error, socket.gaierror):
        return "无法解析填写的路由器地址。请核对地址；未进入 SSH 登录。"
    from .ssh_connect_control import access_denied, connection_errors
    if evidence.get('tcp_connected') is not True and access_denied(error):
        return '本机拒绝了本次网络连接，尚未进入 SSH 认证。请检查本机 IP 安全策略、防火墙或安全软件的连接限制；仅凭此错误不能确定具体原因，也不能判断密码是否正确。'
    errors = connection_errors(error)
    if errors and all(isinstance(item, ConnectionRefusedError) for item in errors):
        return f"目标明确拒绝了 {port} 端口连接。请确认 SSH 服务已开启且端口正确；未进入密码验证。"
    if evidence.get("tcp_connected") is True:
        if isinstance(error, (paramiko.SSHException, EOFError, OSError)):
            if evidence.get("ssh_banner_received") is True:
                return "端口已连通且收到 SSH 服务标识，但 SSH 握手未完成。这不是已确认的密码错误；请导出日志排查。"
            return f"{port} 端口已连通，但未收到有效的 SSH 服务标识。请确认该端口提供 SSH 服务；也可能是服务未及时回应或连接中断，目前不能判断密码。"
    elif isinstance(error, TimeoutError):
        return f"连接 {port} 端口超时，尚未建立到路由器的网络连接。请核对路由器地址、电脑的连接网络和 SSH 端口；仅凭超时无法确定 SSH 是否开启，也不能判定密码错误。"
    return ""
