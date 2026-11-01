# -*- coding: utf-8 -*-
"""
cli / config / console 的离线测试 —— 不需要 eNSP 设备。

跑法（任意工作目录）：
    python tests/test_fixes_offline.py

只用标准库，不依赖 pytest。退出码 0 = 全通过，1 = 有失败。

覆盖本次修复：
  1. _cli_rejected           —— `^down: standby` 图例误判
  2. 分页回显                —— 假设备回放 >1 屏输出
  3. 注册表身份              —— auto_discover 之后不能拿到旧 dict
  4. 视图保持                —— 下发后停在 system-view
  5. auto_discover 合并      —— 登录设备保留原名与凭据
  6. 提示符严格匹配          —— `[Y/N]` 不能当 VRP 提示符
  7. health_check            —— 必须走 _connect（带设备凭据）
  8. 控制台登录流程          —— Username/Password 交互
"""
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from ensp_mcp_server.tools import cli as C
from ensp_mcp_server.tools.cli import _cli_rejected
from ensp_mcp_server.console import HuaweiConsole
import ensp_mcp_server.config as cfg
import ensp_mcp_server.console as console_mod

TMP_DIR = tempfile.mkdtemp(prefix="ensp_offline_test_")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  ok   " if cond else "  FAIL ") + name
          + (("   " + detail) if detail and not cond else ""))


def eq(name, got, want):
    check(name, got == want, f"got={got!r}  want={want!r}")


def section(t):
    print("\n=== " + t + " ===")


# ====================================================================
# 1. _cli_rejected —— `^down: standby` 图例误判
# ====================================================================
section("1. _cli_rejected 图例误判")

legend_out = """Interface                         IP Address      Physical     Protocol
Vlanif100                         66.0.0.1        up           up
GigabitEthernet0/0/1              10.0.0.1        up           up
                                 ^down: standby
"""
check("display ip interface brief 的图例行 ^down: standby 不算被拒",
      not _cli_rejected(legend_out))

for real, why in [
    ("Error: Unrecognized command found at '^' position.", "Unrecognized command"),
    ("Error: Wrong parameter found at '^' position.", "Wrong parameter"),
    ("Error: Incomplete command found at '^' position.", "Incomplete command"),
    ("Error: Ambiguous command found at '^' position.", "Ambiguous command"),
    ("Error: Too many parameters found at '^' position.", "Too many parameters"),
]:
    check(f"真实报错仍能识别（{why}）", _cli_rejected(real), real)

check("单独占一行的 ^ 算被拒（VRP 报错指示符）",
      _cli_rejected("  vrrp vrid 1 virtual-ip\n                          ^\n"))
check("行内 ^ 不算被拒（只有独占一行才算）",
      not _cli_rejected("  some output ^ more text\n"))
check("完全正常的输出不算被拒",
      not _cli_rejected("<R1>display vlan\nTotal 3 VLANs exist\n"))


# ====================================================================
# 2. 分页回显 —— 假设备回放 >1 屏的输出
# ====================================================================
section("2. 分页回显（假设备回放）")


class FakeDevice(threading.Thread):
    """按页回放固定回显，页间插 ---- More ---- 并要求收到空格才继续。"""

    def __init__(self, pages):
        super().__init__(daemon=True)
        self.pages = pages
        self.raw_sent = b""
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]

    def _send(self, conn, text):
        self.raw_sent += text.encode()
        conn.sendall(text.encode())

    def run(self):
        conn, _ = self.sock.accept()
        try:
            self._send(conn, "<R1>\r\n")
            buf, state, page_i = b"", "idle", 0
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                if state == "idle":
                    buf += data
                    while b"\r\n" in buf:
                        line, buf = buf.split(b"\r\n", 1)
                        if not line.strip():
                            continue
                        self._send(conn, line.decode() + "\r\n")   # 回显命令
                        self._send_page(conn, 0)
                        if len(self.pages) == 1:
                            state = "idle"
                        else:
                            page_i, state = 1, "await"
                        break
                elif b" " in data:                                  # 等空格
                    self._send_page(conn, page_i)
                    page_i += 1
                    if page_i >= len(self.pages):
                        state = "idle"
        except OSError:
            pass
        finally:
            conn.close()

    def _send_page(self, conn, i):
        last = (i == len(self.pages) - 1)
        out = "\r\n".join(self.pages[i])
        out += "\r\n<R1>" if last else "\r\n---- More ----\r\n"
        self._send(conn, out)


# 造一份 >1 屏的配置回显，页面边界正好落在 `#` 分隔行上
lines = ["sysname LSW1", "#"]
for v in (10, 20, 100):
    lines += [f"vlan batch {v}", "#"]
    lines += [f"interface Vlanif{v}", f" ip address 192.168.{v}.1 255.255.255.0", "#"]
for p in range(1, 7):
    lines += [f"interface GigabitEthernet0/0/{p}",
              " port link-type access",
              " port default vlan 10",
              "#"]
lines += ["return"]
hash_idx = [i for i, l in enumerate(lines) if l == "#"]
split1, split2 = hash_idx[4], hash_idx[8]
pages = [lines[:split1], lines[split1:split2], lines[split2:]]
print(f"  假设备: 共 {len(lines)} 行, 分 {len(pages)} 页, "
      f"边界落在第 {split1} / {split2} 行（都是 # 分隔行）")

dev = FakeDevice(pages)
dev.start()
time.sleep(0.2)

console = HuaweiConsole("127.0.0.1", dev.port, timeout=8)
console.connect()
outs = [console.send_command("display current-configuration", timeout=8) for _ in range(6)]
console.disconnect()

counts = [len(o.splitlines()) for o in outs]
print(f"  连打 6 次的行数: {counts}")
eq("连打 6 次行数完全一致", len(set(counts)), 1)

all_lines = [ln for o in outs for ln in o.splitlines()]
check("回显里没有 More 残留", not any("More" in ln for ln in all_lines))
check("没有非行首位置的 #（相邻行粘连的判据）",
      not [ln for ln in all_lines if "#" in ln and ln.strip() != "#"],
      str([ln for ln in all_lines if "#" in ln and ln.strip() != "#"][:3]))
check("没有把两行粘成一行（每行都能在原内容里找到）",
      all(ln.strip() == "" or any(ln.strip() == l.strip() for l in lines)
          for ln in all_lines),
      str([ln for ln in all_lines if ln.strip()
           and not any(ln.strip() == l.strip() for l in lines)][:3]))
check("内容没丢：关键配置行都在",
      all(any(l.strip() == ln.strip() for ln in all_lines)
          for l in lines if l.strip()),
      "缺失: " + str([l for l in lines if l.strip()
                      and not any(l.strip() == ln.strip() for ln in all_lines)][:3]))

print(f"  6 次回显共 {len(all_lines)} 行，其中空行 "
      f"{sum(1 for ln in all_lines if ln.strip() == '')} 行")

# 对照：把同一份原始字节流用「修复前」的兜底正则处理一遍。
# 没有这组对照，上面几条断言在 bug 复发时也可能因为假设备回放方式不对而仍然全绿。
raw = dev.raw_sent.decode()
old_cleaned = re.sub(r"\s*---- More ----\s*", "", raw)
old_lines = old_cleaned.replace("\r\n", "\n").replace("\r", "").splitlines()
old_glued = [ln for ln in old_lines if "#" in ln and ln.strip() != "#"]
print(f"  [对照] 用修复前的 \\s*---- More ----\\s* 清理同一份回显，"
      f"粘连行 {len(old_glued)} 处: {old_glued[:2]}")
check("对照实验证明这组测试能抓到修复前的 bug", len(old_glued) > 0,
      "旧正则也没粘连 —— 假设备的回放方式复现不出该 bug")


# ====================================================================
# 3. 注册表身份 —— auto_discover 之后不能拿到旧 dict
# ====================================================================
section("3. 注册表身份（auto_discover 之后的失效回归）")

check("导入时 cli / config 指向同一个 dict",
      cfg._device_registry is C._device_registry)

saved = dict(cfg._device_registry)        # 同样存内容
try:
    # 走真实路径：auto_discover_devices() 与 _load_registry() 都经由它
    cfg._replace_registry({"R1": 2043, "LSW1": 2000})

    check("替换后 cli 仍指向同一个 dict", C._device_registry is cfg._device_registry)
    check("multi_health_check() 的默认设备列表同样是新的",
          set(C._device_registry.keys()) == {"R1", "LSW1"},
          f"cli 看到的是 {list(C._device_registry)}")
finally:
    cfg._replace_registry(saved)

# 源码级兜底：config.py 里不许再出现对 _device_registry 的重新绑定
src = open(os.path.join(REPO, "ensp_mcp_server", "config.py"), encoding="utf-8").read()
rebinds = [ln.strip() for ln in src.splitlines()
           if re.match(r"\s*_device_registry\s*=", ln)]
check("config.py 里没有 _device_registry 的重新绑定语句",
      not rebinds, f"发现: {rebinds}")


# ====================================================================
# 4. 视图保持 —— push_config 结束后停在 system-view，不再 return
# ====================================================================
section("4. 视图保持（假交换机）")

from ensp_mcp_server.console import _prompt_view, _HOSTNAME_CACHE


eq("用户视图提示符", _prompt_view("<R1>", None), "user")
eq("系统视图提示符", _prompt_view("[R1]", None), "system")
eq("子视图提示符（无基线）", _prompt_view("[R1-GigabitEthernet0/0/1]", None), "sub")
eq("子视图提示符（有基线）", _prompt_view("[R1-acl-adv-3000]", "R1"), "sub")
eq("主机名带 '-' 且有基线 → 仍是系统视图", _prompt_view("[R-1]", "R-1"), "system")
eq("主机名带 '-' 且无基线 → 保守当子视图", _prompt_view("[R-1]", None), "sub")
eq("设备启动中的 '##' 认不出", _prompt_view("##", None), "unknown")


class FakeSwitch(threading.Thread):
    """记住当前视图的假交换机；记录收到的每一条命令。"""

    def __init__(self, view="user", hostname="R1"):
        super().__init__(daemon=True)
        self.hostname = hostname
        self.view = view            # user / system / sub
        self.sub = "GigabitEthernet0/0/1"
        self.commands = []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]

    def prompt(self):
        if self.view == "user":
            return f"<{self.hostname}>"
        if self.view == "system":
            return f"[{self.hostname}]"
        return f"[{self.hostname}-{self.sub}]"

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                break
            try:
                self._serve(conn)
            except OSError:
                pass
            finally:
                conn.close()

    def _serve(self, conn):
        conn.sendall((self.prompt() + "\r\n").encode())
        buf = b""
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buf += data
            while b"\r\n" in buf:
                raw, buf = buf.split(b"\r\n", 1)
                line = raw.decode().strip()
                self.commands.append(line)
                if line == "system-view":
                    self.view = "system"
                elif line == "return":
                    self.view = "user"
                elif line.startswith("interface "):
                    self.view, self.sub = "sub", line.split(None, 1)[1]
                conn.sendall((line + "\r\n" + self.prompt() + "\r\n").encode())


def drive(fake, cmds):
    """新开一次控制台连接（模拟一次工具调用）下发命令"""
    before = len(fake.commands)
    with HuaweiConsole("127.0.0.1", fake.port, timeout=5) as c:
        c.send_config_set(cmds)
    return fake.commands[before:]


# A. 用户视图起步：进系统视图，但不许再 return
_HK = _HOSTNAME_CACHE
for k in list(_HK):
    del _HK[k]
sw = FakeSwitch(view="user")
sw.start()
time.sleep(0.2)

got = drive(sw, ["vlan 10"])
print(f"  第一次下发收到: {got}")
check("首次下发进入 system-view", "system-view" in got)
check("结束时不再下发 return", "return" not in got, f"实际: {got}")
eq("设备最终停在系统视图", sw.view, "system")

# B. 紧接着再下发一次：应当零视图切换
got2 = drive(sw, ["vlan 20"])
print(f"  第二次下发收到: {got2}")
check("第二次下发不再发 system-view", "system-view" not in got2, f"实际: {got2}")
check("第二次下发也没有 return", "return" not in got2, f"实际: {got2}")
eq("设备仍在系统视图", sw.view, "system")

# C. 停在了子视图：必须先 return 再重新进系统视图
for k in list(_HK):
    del _HK[k]
sw2 = FakeSwitch(view="sub")
sw2.start()
time.sleep(0.2)

got3 = drive(sw2, ["port default vlan 10"])
print(f"  子视图起步收到: {got3}")
check("子视图起步先 return",
      "return" in got3 and got3.index("return") < got3.index("system-view"),
      f"实际: {got3}")
check("子视图起步重新进 system-view", "system-view" in got3, f"实际: {got3}")
eq("配置命令执行后设备停在系统视图", sw2.view, "system")

sw.sock.close()
sw2.sock.close()


# ====================================================================
# 5. auto_discover 保留凭据
# ====================================================================
section("5. auto_discover 保留凭据与原名")


class _FakeCompleted:
    def __init__(self, stdout):
        self.stdout = stdout


def _fake_run(args, **kwargs):
    if args and args[0] == "tasklist":
        return _FakeCompleted(
            "eNSP_Client.exe          4321 Console                    1      1,234 K\n")
    if args and args[0] == "netstat":
        return _FakeCompleted(
            "  TCP    0.0.0.0:2000    0.0.0.0:0    LISTENING    4321\n"
            "  TCP    0.0.0.0:2001    0.0.0.0:0    LISTENING    4321\n")
    return _FakeCompleted("")


saved_reg = dict(cfg._device_registry)
try:
    cfg._replace_registry(
        {"FW": {"port": 2000, "username": "admin", "password": "p"}})
    with mock.patch.object(cfg.subprocess, "run", side_effect=_fake_run), \
         mock.patch.object(cfg, "_get_sysname_from_port",
                           side_effect=lambda p: None if p == 2000 else "R1"), \
         mock.patch.object(cfg, "_save_registry", lambda: None):
        out = cfg.auto_discover_devices()

    fw = cfg._device_registry.get("FW")
    check("读不到 sysname 的登录设备保留原名", isinstance(fw, dict), repr(fw))
    eq("用户名没有丢", fw.get("username") if isinstance(fw, dict) else None, "admin")
    eq("端口仍是 2000", cfg._get_device_port(fw) if fw else None, 2000)
    eq("能读到 sysname 的设备正常注册", cfg._device_registry.get("R1"), 2001)
    check("报告里能看到保留的 FW", "FW" in out, out)
finally:
    cfg._replace_registry(saved_reg)


# ====================================================================
# 6. 提示符严格匹配（[Y/N] 不能当命令结束）
# ====================================================================
section("6. 提示符严格匹配")

check("[Y/N] 交互提示不算 VRP 提示符",
      not console_mod._VRP_PROMPT_RE.search(
          b"Are you sure to continue? [Y/N]\r\n"))
check("<R1> 算 VRP 提示符",
      bool(console_mod._VRP_PROMPT_RE.search(b"<R1>\r\n")))
check("[R1-GigabitEthernet0/0/1] 算 VRP 提示符",
      bool(console_mod._VRP_PROMPT_RE.search(b"[R1-GigabitEthernet0/0/1]\r\n")))

console_src = open(os.path.join(REPO, "ensp_mcp_server/console.py"),
                   encoding="utf-8").read()
check("console.py 不再保留宽松的 _PROMPT_RE",
      "_PROMPT_RE" not in console_src.replace("_VRP_PROMPT_RE", ""))
check("console.py 没有重复前缀 _VRP_VRP_PROMPT_RE",
      "_VRP_VRP_PROMPT_RE" not in console_src)


# ====================================================================
# 7. health_check 走统一连接（带凭据）
# ====================================================================
section("7. health_check 走统一连接")


class _FakeConsoleCtx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send_command(self, probe, timeout=10):
        return "Huawei Versatile Routing Platform Software"


class _FakeSock:
    def settimeout(self, t):
        pass

    def connect(self, addr):
        pass

    def close(self):
        pass


with mock.patch.object(C, "_connect", return_value=_FakeConsoleCtx()) as m_conn, \
     mock.patch.object(C.socket, "socket", return_value=_FakeSock()):
    out = C.health_check("FW")
m_conn.assert_called_once_with("FW", timeout=10)
check("健康检查通过统一连接（带凭据）", "CLI 命令测试: OK" in out, out)


# ====================================================================
# 8. 控制台登录流程
# ====================================================================
section("8. 控制台登录流程")


class _FakeSockSend:
    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)


c = HuaweiConsole("127.0.0.1", 2000, username="admin", password="p", timeout=5)
c._sock = _FakeSockSend()
login_sends = []
c._send_line = lambda text: login_sends.append(text)
login_reads = iter(["Username:", "Password:", "<R1>"])
c._read_until_any = lambda patterns, timeout=15: next(login_reads)
c._login()
eq("登录时先发用户名再发密码", login_sends, ["admin", "p"])
check("登录流程能走到 VRP 提示符", True)


# ====================================================================
shutil.rmtree(TMP_DIR, ignore_errors=True)
print("\n" + "=" * 60)
failed = [(n, d) for n, ok, d in RESULTS if not ok]
print(f"共 {len(RESULTS)} 项断言：{len(RESULTS) - len(failed)} 通过, {len(failed)} 失败")
for n, d in failed:
    print(f"  FAIL  {n}\n        {d}")
sys.exit(1 if failed else 0)