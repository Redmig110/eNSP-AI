# -*- coding: utf-8 -*-
"""
ensp_app（eNSP 应用与拓扑生命周期）的离线测试 —— 不需要 eNSP 在跑。

跑法（任意工作目录）：
    python tests/test_ensp_app_offline.py

只用标准库，不依赖 pytest。退出码 0 = 全通过，1 = 有失败。

覆盖：
  1. parse_exe_from_shell_command —— 注册表命令行抠 exe 路径
  2. find_ensp_exe                —— ENSP_EXE 环境变量优先级与回退
  3. _handoff_happened            —— 「交接是否真的发生」的判据（含假阳性回归）
  4. _active_ensp_pid             —— 多进程时挑对那个有窗口的（含残留启动器回归）
  5. 错误路径                     —— 文件不存在 / 后缀不对 / 找不到 exe，必须拒绝
  6. 工具注册一致性               —— 4 个工具 TOOLS 与 TOOL_DISPATCH 都在
  7. 源码级兜底                   —— 回显里不能有 GBK 编不出的字符（emoji 那个坑）
  8. 源码级兜底                   —— 不许退回 _ensp_pids()[0]

第 3、4 节是核心：这两个 bug 都**只在特定运行态下出现**，没有 eNSP 就只能在
函数层面复现。第 3 节最后一组用例，用**修复前**的判据处理同一份输入，
确认它确实会返回 True —— 否则这组测试等于没测。
"""
import os
import re
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from ensp_mcp_server.tools import ensp_app as A  # noqa: E402
import ensp_mcp_server.server as S  # noqa: E402

TMP_DIR = tempfile.mkdtemp(prefix="ensp_app_offline_test_")
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
# 1. parse_exe_from_shell_command
# ====================================================================
section("1. parse_exe_from_shell_command 纯解析")

EXE_PARSES = [
    ('"d:\\Program Files\\Huawei\\eNSP\\eNSP_Client.exe" "%1"',
     "d:\\Program Files\\Huawei\\eNSP\\eNSP_Client.exe", "带引号 + %1（注册表真实写法）"),
    ('"C:\\x\\y.exe" %1', "C:\\x\\y.exe", "带引号，%1 无引号"),
    ("C:\\z\\w.exe %1", "C:\\z\\w.exe", "完全不带引号"),
    ("C:\\q.exe", "C:\\q.exe", "只有一个裸路径"),
    ("", "", "空串"),
    ("   ", "", "全空白"),
    ('"unterminated', "", "引号没闭合 -> 拒绝，不瞎猜"),
]
for raw, want, desc in EXE_PARSES:
    eq(f"{desc} -> {want!r}", A.parse_exe_from_shell_command(raw), want)


# ====================================================================
# 2. find_ensp_exe
# ====================================================================
section("2. find_ensp_exe 路径优先级")

fake_exe = os.path.join(TMP_DIR, "eNSP_Client.exe")
with open(fake_exe, "wb") as f:
    f.write(b"MZ")  # 内容无所谓，只看 isfile

saved_env = os.environ.get("ENSP_EXE")
try:
    os.environ["ENSP_EXE"] = fake_exe
    eq("ENSP_EXE 指向真实文件 -> 优先采用", A.find_ensp_exe(), fake_exe)

    os.environ["ENSP_EXE"] = f'"{fake_exe}"'  # 有人会连引号一起粘进去
    eq("ENSP_EXE 带引号也能用", A.find_ensp_exe(), fake_exe)

    os.environ["ENSP_EXE"] = os.path.join(TMP_DIR, "不存在.exe")
    check("ENSP_EXE 指向不存在的文件 -> 不该返回它",
          A.find_ensp_exe() != os.path.join(TMP_DIR, "不存在.exe"))

    # 注册表解析出来的路径若不存在，也不该被采用。
    # 注意别写成 `... if not isfile(...) else ""` —— 两边都是 ""，永远通过。
    reg_path = A._exe_from_registry()
    check("注册表返回的路径必须真实存在（否则应为空）",
          reg_path == "" or os.path.isfile(reg_path), reg_path)
finally:
    if saved_env is None:
        os.environ.pop("ENSP_EXE", None)
    else:
        os.environ["ENSP_EXE"] = saved_env


# ====================================================================
# 3. _handoff_happened —— 「交接是否真的发生」
# ====================================================================
section("3. _handoff_happened 判据")

# 状态形如 (画布数, (各画布设备标签数,))
eq("eNSP 未运行，新实例开来且画布有标签 -> 成功",
   A._handoff_happened(None, (1, (9,))), True)
eq("eNSP 未运行，只起来一个空白板 -> 不算成功",
   A._handoff_happened(None, (1, (0,))), False)
eq("eNSP 未运行，连主框架都没找到 -> 不算成功",
   A._handoff_happened(None, None), False)
eq("运行中 0 画布 -> 交接后出现板 -> 成功",
   A._handoff_happened((0, ()), (1, (9,))), True)
eq("运行中 1 板 -> 又开一个，画布数增加 -> 成功",
   A._handoff_happened((1, (9,)), (2, (9, 9))), True)
eq("运行中空白板 -> 被同文件替换，标签数变化 -> 成功",
   A._handoff_happened((1, (0,)), (1, (9,))), True)

# 这就是假阳性本身：eNSP 已开着 333.topo，请求打开 222.topo，什么都没发生。
# 画布数、标签数一个没变 —— 必须判失败。
eq("回归：运行中 + 非空拓扑，状态毫无变化 -> 必须判失败",
   A._handoff_happened((1, (9,)), (1, (9,))), False)


def _old_predicate(before, after):
    """修复前的判据：只看 after 上有没有设备标签。保留在此作对照。"""
    if after is None:
        return False
    return after[0] >= 1 and sum(after[1]) > 0


check("对照：修复前的判据在同一输入上确实会误报成功（否则这组测试等于没测）",
      _old_predicate((1, (9,)), (1, (9,))) is True)

# ====================================================================
# 4. _active_ensp_pid —— 多进程时挑对那个有窗口的
# ====================================================================
section("4. _active_ensp_pid 挑进程")

saved_pids, saved_main = A._ensp_pids, A._main_window
try:
    # 实测场景：失败的 open_topology 留下一个没有窗口的启动器进程，
    # tasklist 里同时出现两个 eNSP_Client.exe，且启动器排在前面。
    A._ensp_pids = lambda: [36256, 34128]
    A._main_window = lambda pid: 4850040 if pid == 34128 else 0
    eq("残留启动器排在前面时，仍挑中有主框架的那个",
       A._active_ensp_pid(), 34128)

    A._ensp_pids = lambda: [34128]
    A._main_window = lambda pid: 4850040
    eq("只有一个进程时正常返回", A._active_ensp_pid(), 34128)

    A._ensp_pids = lambda: [1, 2]
    A._main_window = lambda pid: 0
    eq("全都没有窗口 -> 0（残留启动器不算 eNSP 在运行）",
       A._active_ensp_pid(), 0)

    A._ensp_pids = lambda: []
    A._main_window = lambda pid: 0
    eq("eNSP 没在跑 -> 0", A._active_ensp_pid(), 0)
finally:
    A._ensp_pids, A._main_window = saved_pids, saved_main


# ====================================================================
# 4b. _pid_alive —— 权限不足不能当成已退出
# ====================================================================
section("4b. _pid_alive 错误路径")


class _FakeKernel32:
    """只实现 _pid_alive 用到的两个调用；OpenProcess 固定失败。"""

    def __init__(self, last_error):
        self.last_error = last_error

    def OpenProcess(self, access, inherit, pid):
        return 0

    def GetLastError(self):
        return self.last_error


saved_k32 = A._k32
try:
    A._k32 = _FakeKernel32(A.ERROR_INVALID_PARAMETER)
    eq("OpenProcess 参数无效（进程不存在）-> 已退出", A._pid_alive(123), False)

    A._k32 = _FakeKernel32(5)  # ERROR_ACCESS_DENIED
    eq("OpenProcess 拒绝访问 -> 视为仍存活", A._pid_alive(123), True)
finally:
    A._k32 = saved_k32


# ====================================================================
# 5. 错误路径 —— 必须拒绝，不能瞎试
# ====================================================================
section("5. 错误路径")

out = A.open_topology(os.path.join(TMP_DIR, "根本不存在.topo"))
check("拓扑文件不存在 -> 拒绝并说明", "不存在" in out, out)

not_topo = os.path.join(TMP_DIR, "其实是文本.txt")
with open(not_topo, "w", encoding="utf-8") as f:
    f.write("hello")
out = A.open_topology(not_topo)
check("后缀不是 .topo -> 拒绝", "不是 .topo 文件" in out, out)

# 找不到 exe 时，open_ensp / open_topology 都该停手（绝不能真的去启动进程）
saved_find, saved_active = A.find_ensp_exe, A._active_ensp_pid
try:
    A.find_ensp_exe = lambda: ""
    A._active_ensp_pid = lambda: 0
    out = A.open_ensp()
    check("找不到 exe -> open_ensp 报错并提到 ENSP_EXE", "ENSP_EXE" in out, out)

    real_topo = os.path.join(TMP_DIR, "真的.topo")
    with open(real_topo, "w", encoding="utf-8") as f:
        f.write("<topo/>")
    out = A.open_topology(real_topo)
    check("找不到 exe -> open_topology 报错并提到 ENSP_EXE", "ENSP_EXE" in out, out)
finally:
    A.find_ensp_exe, A._active_ensp_pid = saved_find, saved_active


# ====================================================================
# 6. 工具注册一致性
# ====================================================================
section("6. 工具注册一致性")

names = {t.name for t in S.TOOLS}
disp = set(S.TOOL_DISPATCH)
NEW_TOOLS = ["open_ensp", "close_ensp", "open_topology", "close_topology"]
for n in NEW_TOOLS:
    check(f"{n} 在 TOOLS 里", n in names)
    check(f"{n} 在 TOOL_DISPATCH 里", n in disp)
check("没有「有定义无分发」的工具", not (names - disp), f"{(names - disp)}")
check("没有「有分发无定义」的工具", not (disp - names), f"{(disp - names)}")


# ====================================================================
# 7. 源码级兜底：回显里不能有 GBK 编不出的字符
# ====================================================================
section("7. 源码级兜底：GBK 可编码性")

# 实测踩过：回显里写了 ❌ / ⚠，测试脚本往 GBK 控制台打就 UnicodeEncodeError。
for rel in ("ensp_mcp_server/tools/ensp_app.py", "ensp_mcp_server/server.py"):
    src = open(os.path.join(REPO, rel), encoding="utf-8").read()
    bad = []
    for i, ch in enumerate(src):
        if ord(ch) > 127:
            try:
                ch.encode("gbk")
            except UnicodeEncodeError:
                bad.append((src[:i].count("\n") + 1, hex(ord(ch))))
    check(f"{rel} 里没有 GBK 编不出的字符", not bad, f"行/码点: {bad}")


# ====================================================================
# 8. 源码级兜底：不许退回 _ensp_pids()[0]
# ====================================================================
section("8. 源码级兜底：不许按顺序取第一个 pid")

src = open(os.path.join(REPO, "ensp_mcp_server/tools/ensp_app.py"), encoding="utf-8").read()
# 先剥掉三引号块 —— 文档字符串里正好在讲这条规则本身，不剥会自摆乌龙。
code = re.sub(r'""".*?"""', "", src, flags=re.S)
# 任何 pids[0] / _ensp_pids()[0] 都是回归：残留启动器没有主框架窗口，
# 按顺序取第一个会把“没在运行”误判成“已在运行”。
offenders = []
for ln in code.splitlines():
    s = ln.strip()
    if s.startswith("#"):
        continue
    if re.search(r"_ensp_pids\(\)\s*\[0\]", s) or re.search(r"\bpids\[0\]", s):
        offenders.append(s)
check("没有按顺序取第一个 eNSP 进程的用法", not offenders, f"发现: {offenders}")


# ====================================================================
print("\n" + "=" * 60)
failed = [(n, d) for n, ok, d in RESULTS if not ok]
print(f"共 {len(RESULTS)} 项断言：{len(RESULTS) - len(failed)} 通过, {len(failed)} 失败")
for n, d in failed:
    print(f"  FAIL  {n}\n        {d}")
sys.exit(1 if failed else 0)
