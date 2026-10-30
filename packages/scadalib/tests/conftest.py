import random
import socket


def free_port() -> int:
    """挑一个可绑定的端口。

    不用 bind(0)：WSL mirrored 网络下 Windows 保留端口段与 Linux 临时端口范围冲突，bind(0) 会失败。
    """
    for _ in range(200):
        port = random.randint(20000, 40000)
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port")
