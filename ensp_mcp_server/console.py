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

# ANSI/VT100 转义序列。设备在行编辑时会发 \x1b[42D + 空格 + \x1b[42D 重画整行，
# 不过滤的话会混进 display current-configuration 之类的输出里。
#   覆盖 CSI (\x1b[ + 参数 + 终止符) 和双字节转义 (\x1b + 单字符)。
_ANSI_RE = re.compile(rb'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]')
_ANSI_STR_RE = re.compile('\x1b\\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\\\-_]')


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


def _strip_ansi(data: bytes) -> bytes:
    """过滤 ANSI/VT100 转义序列（字节层面）"""
    return _ANSI_RE.sub(b'', data)


def _sanitize(data: bytes) -> bytes:
    """过滤 Telnet IAC + ANSI 转义，得到可直接解码的干净字节"""
    return _strip_ansi(_strip_iac(data))


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
        如果设备已在 system-view（提示符为 [xxx]），则跳过进入。
        """
        all_output = []

        # 检查当前提示符，判断是否已在 system-view
        # 先发送回车获取当前提示符
        self._send_line('')
        prompt_output = self._read_until_prompt(timeout=3)
        all_output.append(prompt_output)

        # 如果提示符是 <xxx> 格式（用户视图），需要进入 system-view
        # 如果提示符是 [xxx] 格式（系统视图），跳过
        if re.search(rb'<\S+>', prompt_output.encode('utf-8', errors='ignore')):
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
                chunk = _sanitize(chunk)
                buf += chunk

                # 处理 More 分页：发送空格继续
                if _MORE_RE.search(buf):
                    self._sock.sendall(b' ')
                    # 就地摘掉 More 标记。留到最后再删的话，下面的兜底清理
                    # 会连标记周围的换行一起吃掉，把相邻两行粘成一行 ——
                    # 凡是超过一屏的 display 输出都会被这么污染。
                    buf = _MORE_RE.sub(b'', buf)
                    continue

                # 检测提示符后等待一小段时间，确认没有更多数据
                # 这解决了 AR 路由器命令回显中包含提示符的问题
                if _PROMPT_RE.search(buf):
                    # 等待 0.3 秒看是否有更多数据
                    self._sock.settimeout(0.3)
                    try:
                        extra = self._sock.recv(4096)
                        if extra:
                            extra = _sanitize(extra)
                            buf += extra
                            # 如果收到更多数据，继续主循环
                            self._sock.settimeout(1.0)
                            continue
                    except socket.timeout:
                        # 没有更多数据，提示符是真正的结束标记
                        pass
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
        # 兜底再滤一遍 ANSI：字节级过滤按 chunk 进行，转义序列若跨 chunk 被切开会漏网
        text = _ANSI_STR_RE.sub('', text)
        # 兜底清理偶发漏网的 More 残留。[^\S\n] 表示「除换行外的空白」——
        # 这里绝不能用 \s，否则会把标记两边的换行也删掉，导致相邻行粘连。
        text = re.sub(r'[^\S\n]*---- More ----[^\S\n]*\n?', '', text)
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
                buf += _sanitize(chunk)
        except (socket.timeout, OSError):
            pass
        self._sock.settimeout(self.timeout)
        text = buf.decode('utf-8', errors='ignore')
        text = _ANSI_STR_RE.sub('', text)
        text = text.replace('\r\n', '\n').replace('\r', '')
        return text
