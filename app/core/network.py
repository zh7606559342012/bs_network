# app/core/network.py
import socket


def get_hostname() -> str:
    """
    获取主机名
    对应 Go 的 os.Hostname()
    """
    try:
        return socket.gethostname()
    except Exception as e:
        raise RuntimeError(f"get hostname failed: {e}")