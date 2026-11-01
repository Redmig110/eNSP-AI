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

# ---- More ---- 分页提示
_MORE_RE = re.compile(rb'----\s*More\s*----')

# 控制台登录提示。USG6000V 这类设备的控制台要求先登录，交换机则直接给提示符。
_LOGIN_USER_RE = re.compile(rb'Username:\s*$', re.M)
_LOGIN_PASS_RE = re.compile(rb'Password:\s*$', re.M)
_LOGIN_CHANGE_RE = re.compile(rb'Change now\?\s*\[Y/N\]', re.M)
_LOGIN_FAIL_RE = re.compile(rb'Authentication fail|Login failed|Username:\s*$', re.M)
# 登录后设备会先显示 "Please Press ENTER."，不回车的话会很快掉回登录页
_LOGIN_ENTER_RE = re.compile(rb'Please\s+Press\s+ENTER', re.I)
# 提示符的严格版本：不能把 "[Y/N]" 这种交互提示误当成 VRP 提示符
_VRP_PROMPT_RE = re.compile(rb'[<\[](?!\s*[YyNn]\s*/\s*[YyNn]\s*\])[^<>\[\]\r\n]+[>\]]\s*$', re.M)

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


# 每台设备最近一次处在用户视图/系统视图时的提示符内容（即主机名）。
# 控制台断开重连后设备仍停在原来的视图上，而 system-view 与子视图的提示符
# 都长成 [xxx]，只能靠主机名基线区分。键是 (host, port)。
_HOSTNAME_CACHE: dict[tuple[str, int], str] = {}


def _prompt_view(prompt: str, known_hostname: str | None) -> str:
    """
    判断提示符所处视图，返回 'user' / 'system' / 'sub' / 'unknown'。

    子视图（如 [R1-GigabitEthernet0/0/1]）必须和系统视图 [R1] 区分开，
    否则后续配置命令会打进接口/协议视图里。
    """
    prompt = prompt.strip()
    if len(prompt) < 2:
        return 'unknown'
    if prompt.startswith('<') and prompt.endswith('>'):
        return 'user'
    if not (prompt.startswith('[') and prompt.endswith(']')):
        return 'unknown'

    inner = prompt[1:-1]
    if known_hostname:
        # 有基线时以主机名为准，主机名里带 '-' 也不会误判
        return 'system' if inner == known_hostname else 'sub'
    # 没有基线时的保守判断：VRP 子视图提示符一定带 '-'。
    # 主机名恰好含 '-' 时会被当成子视图，最多多发两条命令，结果仍然正确。
    return 'sub' if '-' in inner else 'system'


class HuaweiConsole:
    """
    轻量级华为 eNSP 控制台客户端

    用法:
        with HuaweiConsole("127.0.0.1", 2000) as console:
            output = console.send_command("display ip interface brief")
    """

    def __init__(self, host: str, port: int, timeout: int = 15,
                 username: str = "", password: str = ""):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.username = username
        self.password = password
        self._sock: socket.socket | None = None

    def connect(self):
        """建立 TCP 连接并等待设备提示符"""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect((self.host, self.port))
        # 发送回车唤醒控制台，等待提示符
        time.sleep(0.3)
        if self.username:
            self._login()
        else:
            self._sock.sendall(b'\r\n')
            self._read_until_prompt(timeout=5)
        logger.debug("已连接 %s:%d", self.host, self.port)

    def _login(self):
        """
        控制台登录。

        eNSP 里交换机是免登录的，USG6000V 却要走 `Username:` / `Password:`。
        首次登录还会要求改密，这里一律答 N（保持原口令），
        免得每次连接都卡在改密交互上。
        """
        self._sock.sendall(b'\r\n')
        banner = self._read_until_any(
            [_LOGIN_USER_RE, _LOGIN_PASS_RE, _VRP_PROMPT_RE, _LOGIN_ENTER_RE], timeout=6)

        if _VRP_PROMPT_RE.search(banner.encode('utf-8', errors='ignore')):
            return  # 已经登录过 / 本来就不需要登录

        if _LOGIN_ENTER_RE.search(banner.encode('utf-8', errors='ignore')):
            # 上一次会话停在 "Please Press ENTER."，回车后重新看
            self._sock.sendall(b'\r\n')
            banner = self._read_until_any(
                [_LOGIN_USER_RE, _LOGIN_PASS_RE, _VRP_PROMPT_RE], timeout=6)
            if _VRP_PROMPT_RE.search(banner.encode('utf-8', errors='ignore')):
                return

        # 只有本连接还停在 Username: 时才需要输用户名；
        # 若已经停在 Password:（上一次登录被打断），直接给密码。
        if _LOGIN_USER_RE.search(banner.encode('utf-8', errors='ignore')):
            self._send_line(self.username)
            self._read_until_any([_LOGIN_PASS_RE], timeout=6)

        self._send_line(self.password)
        self._finish_login()

    def _finish_login(self):
        """处理登录后的改密提示 / Please Press ENTER，直到回到 VRP 提示符"""
        for _ in range(4):
            out = self._read_until_any(
                [_VRP_PROMPT_RE, _LOGIN_CHANGE_RE, _LOGIN_ENTER_RE, _LOGIN_FAIL_RE],
                timeout=10)
            b = out.encode('utf-8', errors='ignore')

            if _LOGIN_CHANGE_RE.search(b):
                # 首次登录被要求改密。答 N 会让会话直接退回登录页，
                # 所以这里不擅自改密，也不装作成功 —— 报错让人去处理一次。
                raise ConnectionError(
                    f"设备 {self.host}:{self.port} 要求首次登录修改密码，"
                    f"请先在 GUI 里手工改密，再把新口令写进 devices.json")
            if _VRP_PROMPT_RE.search(b):
                logger.debug("已登录 %s:%d", self.host, self.port)
                return
            if _LOGIN_ENTER_RE.search(b):
                self._sock.sendall(b'\r\n')
                continue
            if _LOGIN_FAIL_RE.search(b):
                raise ConnectionError(
                    f"设备 {self.host}:{self.port} 登录失败，"
                    f"请检查 devices.json 里的用户名/密码")

        raise ConnectionError(f"设备 {self.host}:{self.port} 登录后未回到提示符")

    def _read_until_any(self, patterns: list, timeout: int = 15) -> str:
        """读到缓冲命中任一正则为止（登录这类交互用，没有 VRP 提示符可等）"""
        buf = b''
        deadline = time.monotonic() + timeout
        self._sock.settimeout(0.5)
        while time.monotonic() < deadline:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf += _sanitize(chunk)
            if any(p.search(buf) for p in patterns):
                break
        self._sock.settimeout(self.timeout)
        return buf.decode('utf-8', errors='ignore').replace('\r\n', '\n')

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
        if lines and _VRP_PROMPT_RE.search(lines[-1].encode('utf-8', errors='ignore')):
            lines = lines[:-1]
        return '\n'.join(lines)

    def send_config_set(self, commands: list[str]) -> str:
        """
        发送一组配置命令

        自动确保落在 system-view 后执行，**结束时不再 return** —— 设备就留在
        系统视图里。控制台重连后视图仍然保持，所以下一次下发可以直接接着用，
        省掉一次 system-view 往返。若设备停在子视图（下发过 interface / acl
        等命令），会先 return 再重新进系统视图。
        """
        all_output = [self._ensure_system_view()]

        for cmd in commands:
            self._send_line(cmd)
            all_output.append(self._read_until_prompt(timeout=5))

        return '\n'.join(all_output)

    def _read_prompt(self) -> str:
        """发一个回车，取回显里最后一个非空行（即当前提示符）"""
        self._send_line('')
        output = self._read_until_prompt(timeout=3)
        for line in reversed(output.splitlines()):
            if line.strip():
                return line.strip()
        return ''

    def _ensure_system_view(self) -> str:
        """确保设备处于 system-view，返回过程回显"""
        prompt = self._read_prompt()
        key = (self.host, self.port)
        view = _prompt_view(prompt, _HOSTNAME_CACHE.get(key))

        if view == 'user':
            # 记住主机名，作为后续区分系统视图 / 子视图的基线
            _HOSTNAME_CACHE[key] = prompt[1:-1]
            self._send_line('system-view')
            return self._read_until_prompt(timeout=5)

        if view == 'system':
            return ''  # 已经在系统视图，什么都不用做

        if view == 'sub':
            self._send_line('return')
            first = self._read_until_prompt(timeout=5)
            self._send_line('system-view')
            second = self._read_until_prompt(timeout=5)
            return first + second

        # 认不出提示符（比如设备还在启动），交给后面的命令自己报错
        return ''

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
                if _VRP_PROMPT_RE.search(buf):
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
                if _VRP_PROMPT_RE.search(buf):
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
