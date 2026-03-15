"""
轻量级华为 eNSP 控制台客户端

通过原生 socket 直连 eNSP 控制台端口（无需登录认证）。
替代 Netmiko 的 huawei_telnet，解决 eNSP 无认证提示符导致连接失败的问题。
"""
import logging
import re
import socket
import time

logger = logging.getLogger("ensp_mcp.console")

# 华为提示符: <Huawei> 或 [Huawei] 或 <R1> 或 [R1-GigabitEthernet0/0/0] 等
_PROMPT_RE = re.compile(rb'[<\[]\S+[>\]]\s*$')

# ---- More ---- 分页提示
_MORE_RE = re.compile(rb'----\s*More\s*----')

# Telnet IAC 序列（3字节: \xff + cmd + option）
_IAC = 0xFF
_WILL = 0xFB
_WONT = 0xFC
_DO = 0xFD
_DONT = 0xFE


def _strip_iac(data: bytes) -> bytes:
    """过滤 Telnet IAC 序列"""
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == _IAC and i + 2 < len(data):
            # 跳过 3 字节 IAC 序列
            i += 3
        else:
            result.append(data[i])
            i += 1
    return bytes(result)


class HuaweiConsole:
    """
    轻量级华为 eNSP 控制台客户端

    用法:
        with HuaweiConsole("127.0.0.1", 2000) as console:
            output = console.send_command("display ip interface brief")
    """

    def __init__(self, host: str, port: int, timeout: int = 15):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None

    def connect(self):
        """建立 TCP 连接并等待设备提示符"""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect((self.host, self.port))
        # 发送回车唤醒控制台，等待提示符
        time.sleep(0.3)
        self._sock.sendall(b'\r\n')
        self._read_until_prompt(timeout=5)
        logger.debug("已连接 %s:%d", self.host, self.port)

    def disconnect(self):
        """关闭连接"""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()

    def send_command(self, command: str, timeout: int = 0) -> str:
        """
        发送显示命令并等待提示符返回

        自动处理 ---- More ---- 分页。
        """
        t = timeout or self.timeout
        self._send_line(command)
        output = self._read_until_prompt(timeout=t)
        # 去掉回显的命令行本身
        lines = output.splitlines()
        if lines and command.strip() in lines[0]:
            lines = lines[1:]
        # 去掉末尾的提示符行
        if lines and _PROMPT_RE.search(lines[-1].encode('utf-8', errors='ignore')):
            lines = lines[:-1]
        return '\n'.join(lines)

    def send_config_set(self, commands: list[str]) -> str:
        """
        发送一组配置命令

        自动进入 system-view，执行完后 return 退出。
        """
        all_output = []

        # 进入系统视图
        self._send_line('system-view')
        all_output.append(self._read_until_prompt(timeout=5))

        for cmd in commands:
            self._send_line(cmd)
            all_output.append(self._read_until_prompt(timeout=5))

        # 退回用户视图
        self._send_line('return')
        all_output.append(self._read_until_prompt(timeout=5))

        return '\n'.join(all_output)

    def send_command_timing(self, command: str, delay: float = 2.0) -> str:
        """
        发送命令，等待固定时间后读取输出（用于交互式命令如 save）
        """
        self._send_line(command)
        time.sleep(delay)
        return self._read_available()

    # ---- 内部方法 ----

    def _send_line(self, text: str):
        """发送一行文本"""
        self._sock.sendall((text + '\r\n').encode('utf-8'))

    def _read_until_prompt(self, timeout: int = 15) -> str:
        """读取直到检测到提示符，自动处理 More 分页"""
        buf = b''
        deadline = time.monotonic() + timeout
        self._sock.settimeout(1.0)

        while time.monotonic() < deadline:
            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                chunk = _strip_iac(chunk)
                buf += chunk

                # 处理 More 分页：发送空格继续
                if _MORE_RE.search(buf.split(b'\n')[-1] if buf else b''):
                    self._sock.sendall(b' ')
                    # 清掉 More 提示本身（设备会用回车覆盖）
                    continue

                # 检测提示符
                if _PROMPT_RE.search(buf):
                    break
            except socket.timeout:
                # 超时但还没到 deadline，继续等
                if _PROMPT_RE.search(buf):
                    break
                continue
            except OSError:
                break

        self._sock.settimeout(self.timeout)
        text = buf.decode('utf-8', errors='ignore')
        # 清理 More 残留
        text = re.sub(r'\s*---- More ----\s*', '', text)
        # 清理回车符
        text = text.replace('\r\n', '\n').replace('\r', '')
        return text

    def _read_available(self) -> str:
        """非阻塞读取当前缓冲区所有可用数据"""
        buf = b''
        self._sock.settimeout(0.5)
        try:
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                buf += _strip_iac(chunk)
        except (socket.timeout, OSError):
            pass
        self._sock.settimeout(self.timeout)
        text = buf.decode('utf-8', errors='ignore')
        text = text.replace('\r\n', '\n').replace('\r', '')
        return text
