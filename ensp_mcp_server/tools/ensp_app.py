"""
eNSP 应用与拓扑的生命周期控制。

只用 stdlib ctypes 调 Win32，不引入 pywin32 —— 与 console.py 用原生 socket、
config.py 用 tasklist 的风格一致。

实测结论（2026-10-17，见 logs/dev/开发记录.md）。**注意：下面第 4、5 条与最初的
猜测相反，是踩过坑之后改过来的，别再按直觉改回去。**

1. **eNSP 是单实例**。带 .topo 路径启动第二个进程时，新进程会立刻自己退出
   （或反过来，在上一个实例已退出时自己成为新的实例）。
2. 裸启动 = 开一个**空白 topoboard**（画布 `AfxFrameOrView80su` 存在，但其下
   没有设备名标签子窗口）。
3. 拓扑画布是主框架的直接子窗口里，类名 `AfxFrameOrView80su`、可见的那个。
   设备名标签是它的后代，类名 `RichEdit20W`（333.topo 6 台设备 -> 9 个标签）。
4. 给**画布**发 `WM_CLOSE` 只关拓扑，eNSP 主框架与进程存活（实测存活 12 秒）。
   **关掉最后一个拓扑并不会让 eNSP 退出** —— 这条曾被误判过一次。
5. **`open_topology` 的成功判据只能是"画布状态变化"，不能是"画布上有设备标签"。**
   后者在 eNSP 已经开着别的拓扑时恒为真，会把"根本没打开"静默误报成成功。
   实测：eNSP 已在运行时，命令行交接基本不生效（已有非空拓扑 -> 无效；
   一个画布都没有 -> 超时；只有空白板时成功过 1 次，未复现）。
6. **`close_ensp` 只在没有打开拓扑时才干脆**（1~5 秒退出）。只要 eNSP 里开着拓扑，
   关闭就会弹模态确认框，`WM_CLOSE` 会被挡住 —— 工具靠检测 `#32770` 对话框
   把原因说清楚，但**不替用户点按钮、不 taskkill**（用户明确要求）。
7. **残留坑**：关掉拓扑或关掉 eNSP 之后，`eNSP_Switch.exe` / `VBoxHeadless.exe`
   可能残留且控制台端口消失 —— 即"进程还在但 MCP 连不上"的僵尸态。
   这是 eNSP 自身行为，本模块只报告、不清理。
"""
import ctypes
import logging
import os
import re
import subprocess
import time
from ctypes import wintypes

logger = logging.getLogger("ensp_mcp.ensp_app")

# ==================== Win32 常量 ====================

_u32 = ctypes.windll.user32
_k32 = ctypes.windll.kernel32

WM_CLOSE = 0x0010
GW_CHILD = 5
GW_HWNDNEXT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

_ENSP_PROCESS = "eNSP_Client.exe"
# 关掉 eNSP 后可能残留的设备进程（报告用，不清理）
_DEVICE_PROCESSES = ("eNSP_Switch.exe", "VBoxHeadless.exe", "eNSP_PCServer.exe")

_CANVAS_CLASS = "AfxFrameOrView80su"
_LABEL_CLASS = "RichEdit20W"

_ENUM_CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


# ==================== Win32 薄封装 ====================

def _class_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _u32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _title_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    _u32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _top_level_windows() -> list[int]:
    res: list[int] = []

    def cb(hwnd, _):
        res.append(hwnd)
        return True

    _u32.EnumWindows(_ENUM_CB(cb), 0)
    return res


def _all_descendants(hwnd: int) -> list[int]:
    res: list[int] = []

    def cb(h, _):
        res.append(h)
        return True

    _u32.EnumChildWindows(hwnd, _ENUM_CB(cb), 0)
    return res


def _direct_children(hwnd: int) -> list[int]:
    out = []
    h = _u32.GetWindow(hwnd, GW_CHILD)
    while h:
        out.append(h)
        h = _u32.GetWindow(h, GW_HWNDNEXT)
    return out


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    _u32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _area(hwnd: int) -> int:
    l, t, r, b = _rect(hwnd)
    return max(0, r - l) * max(0, b - t)


def _is_window(hwnd: int) -> bool:
    return bool(_u32.IsWindow(hwnd))


def _is_visible(hwnd: int) -> bool:
    return bool(_u32.IsWindowVisible(hwnd))


def _post_close(hwnd: int) -> None:
    _u32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def _pid_alive(pid: int) -> bool:
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    code = wintypes.DWORD()
    ok = _k32.GetExitCodeProcess(h, ctypes.byref(code))
    _k32.CloseHandle(h)
    return bool(ok) and code.value == STILL_ACTIVE


# ==================== 进程枚举 ====================

def _tasklist_processes() -> dict[str, list[int]]:
    """返回 {进程名: [pid, ...]}，返回空字典表示拿不到。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="ignore",
        ).stdout
    except Exception as e:
        logger.warning("tasklist 执行失败: %s", e)
        return {}

    procs: dict[str, list[int]] = {}
    for line in out.splitlines():
        m = re.match(r'"([^"]+)","(\d+)"', line.strip())
        if m:
            procs.setdefault(m.group(1), []).append(int(m.group(2)))
    return procs


def _ensp_pids() -> list[int]:
    return _tasklist_processes().get(_ENSP_PROCESS, [])


def _active_ensp_pid() -> int:
    """真正拥有主框架窗口的那个 eNSP 进程 PID，没有则返回 0。

    必须用这个而不是 `_ensp_pids()[0]`：实测 `open_topology` 失败时会残留一个
    **没有窗口的启动器进程**，此时列表里会同时出现两个 eNSP_Client.exe，
    按顺序取第一个会挑错人。
    """
    pids = _ensp_pids()
    for p in pids:
        if _main_window(p):
            return p
    return pids[0] if pids else 0


def _leftover_device_processes() -> dict[str, int]:
    procs = _tasklist_processes()
    return {name: len(procs.get(name, [])) for name in _DEVICE_PROCESSES if procs.get(name)}


# ==================== exe 路径定位 ====================

def parse_exe_from_shell_command(cmd: str) -> str:
    """从注册表 shell\\open\\command 的值里抠出 exe 路径。纯函数，可单测。

        '"d:\\...\\eNSP_Client.exe" "%1"'  -> 'd:\\...\\eNSP_Client.exe'
        'd:\\...\\eNSP_Client.exe %1'      -> 'd:\\...\\eNSP_Client.exe'
    """
    cmd = (cmd or "").strip()
    if not cmd:
        return ""
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end == -1:
            return ""
        return cmd[1:end]
    # 没引号：截到第一个 "%1" 或第一个空格
    cut = cmd.find('"')
    if cut > 0:
        return cmd[:cut].strip()
    return cmd.split(" ")[0].strip()


def _exe_from_registry() -> str:
    try:
        import winreg
    except ImportError:  # 非 Windows，理论上到不了这里
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"topofile\shell\open\command") as k:
            cmd, _ = winreg.QueryValueEx(k, "")
    except OSError:
        return ""
    path = parse_exe_from_shell_command(cmd)
    return path if path and os.path.isfile(path) else ""


def _exe_from_common_dirs() -> str:
    rel = os.path.join("Huawei", "eNSP", _ENSP_PROCESS)
    roots = []
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        v = os.environ.get(var)
        if v:
            roots.append(v)
    # 常见盘符兜底（eNSP 可能装在非系统盘，实测本机就在 D:）
    for drive in "CDEFGH":
        roots.append(f"{drive}:\\Program Files")
        roots.append(f"{drive}:\\Program Files (x86)")
    seen = set()
    for root in roots:
        cand = os.path.join(root, rel)
        if cand in seen:
            continue
        seen.add(cand)
        if os.path.isfile(cand):
            return cand
    return ""


def find_ensp_exe() -> str:
    """定位 eNSP_Client.exe：ENSP_EXE 环境变量 → 注册表文件关联 → 常见目录。

    找不到返回 ""，由调用方报错 —— 不猜路径。
    """
    env = os.environ.get("ENSP_EXE", "").strip().strip('"')
    if env and os.path.isfile(env):
        return env
    reg = _exe_from_registry()
    if reg:
        return reg
    return _exe_from_common_dirs()


# ==================== 窗口定位 ====================

def _main_window(pid: int) -> int:
    """eNSP 主框架：该类进程下类名以 'Afx:' 开头的顶层窗口。"""
    for h in _top_level_windows():
        if _pid_of(h) == pid and _class_of(h).startswith("Afx:"):
            return h
    return 0


def _canvas_windows(main_hwnd: int) -> list[int]:
    """主框架的直接子窗口里，可见的拓扑画布（按面积降序）。"""
    cands = [
        c for c in _direct_children(main_hwnd)
        if _class_of(c) == _CANVAS_CLASS and _is_visible(c)
    ]
    cands.sort(key=_area, reverse=True)
    return cands


def _count_device_labels(canvas_hwnd: int) -> int:
    """画布上的设备名标签个数。空白板为 0，333.topo（6 台设备）实测为 9。"""
    return sum(1 for c in _all_descendants(canvas_hwnd) if _class_of(c) == _LABEL_CLASS)


def _snapshot(pid: int) -> str:
    """一行窗口状态摘要，用于回显证据。"""
    main = _main_window(pid)
    if not main:
        return "主框架窗口: 未找到"
    canvases = _canvas_windows(main)
    if not canvases:
        return "主框架窗口: 已找到 | 拓扑画布: 0 个"
    labels = [_count_device_labels(c) for c in canvases]
    return (
        f"主框架窗口: 已找到 | 拓扑画布: {len(canvases)} 个 "
        f"| 各画布设备标签数: {labels}"
    )


def _board_state(pid: int) -> tuple[int, tuple[int, ...]] | None:
    """当前画布状态 (画布数, 各画布设备标签数)。主框架找不到时返回 None。

    这是判断"交接是否真的发生"的唯一可靠依据 —— 实测不能靠"画布上有标签"
    这种绝对量判据：eNSP 里本来就开着别的拓扑时它恒为真，会静默误判成成功。
    """
    main = _main_window(pid)
    if not main:
        return None
    canvases = _canvas_windows(main)
    return len(canvases), tuple(_count_device_labels(c) for c in canvases)


def _modal_dialogs(pid: int) -> list[tuple[int, str]]:
    """该进程下可见的 #32770 对话框 [(hwnd, 标题)]。用于识别挡住关闭的确认框。"""
    out = []
    for h in _top_level_windows():
        if _pid_of(h) == pid and _class_of(h) == "#32770" and _is_visible(h):
            out.append((h, _title_of(h)))
    return out


def _leftover_note() -> str:
    left = _leftover_device_processes()
    if not left:
        return ""
    detail = "、".join(f"{n}×{c}" for n, c in left.items())
    return (
        f"\n注意：检测到残留设备进程 {detail}。"
        "这些进程可能已失去控制台端口（MCP 连不上），本工具按设计不清理它们。"
    )


# ==================== 工具实现 ====================

def open_ensp() -> str:
    """启动 eNSP。已在运行则不重复启动。"""
    pid = _active_ensp_pid()
    if pid:
        return (
            f"eNSP 已在运行（PID {pid}），未重复启动。\n{_snapshot(pid)}\n"
            "提示：eNSP 是单实例的，重复启动会让新进程秒退并把请求转交给现有实例。"
        )

    exe = find_ensp_exe()
    if not exe:
        return (
            "未找到 eNSP_Client.exe。已尝试：ENSP_EXE 环境变量、"
            "注册表 HKCR\\topofile\\shell\\open\\command、常见安装目录。\n"
            "请设置环境变量 ENSP_EXE 指向 eNSP_Client.exe 的完整路径后重试。"
        )

    try:
        proc = subprocess.Popen(
            [exe],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            cwd=os.path.dirname(exe),
        )
    except Exception as e:
        return f"启动 eNSP 失败: {e}"

    # 等主框架窗口出现 —— eNSP 本身不慢，但别设太短
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(0.5)
        if _main_window(proc.pid):
            time.sleep(1)  # 让画布控件建起来
            return (
                f"eNSP 已启动（PID {proc.pid}）。\n{_snapshot(proc.pid)}\n"
                "提示：裸启动得到的是一个空白 topoboard，设备不会自动运行。"
            )

    return (
        f"已启动 eNSP 进程（PID {proc.pid}），但 30 秒内没等到主窗口出现。\n"
        "请到桌面上确认 eNSP 界面是否正常。"
    )


def close_ensp() -> str:
    """优雅关闭 eNSP：给主框架发 WM_CLOSE，不做 taskkill。"""
    pid = _active_ensp_pid()
    if not pid:
        note = _leftover_note()
        return f"eNSP 未在运行。{note}" if note else "eNSP 未在运行。"

    main = _main_window(pid)
    if not main:
        return (
            f"eNSP 进程在（PID {pid}），但没找到 Afx 主框架窗口，无法发送关闭请求。"
            "请在 GUI 里手动关闭。"
        )

    _post_close(main)
    deadline = time.time() + 15
    while time.time() < deadline:
        time.sleep(0.5)
        if not _pid_alive(pid):
            note = _leftover_note()
            return f"eNSP 已关闭（PID {pid}）。{note}"

    dialogs = _modal_dialogs(pid)
    if dialogs:
        detail = "、".join(f"hwnd={h} 标题={t!r}" for h, t in dialogs)
        return (
            f"已向 eNSP 主窗口发送关闭请求，但 15 秒后进程（PID {pid}）仍未退出。\n"
            f"原因已定位：屏幕上有一个 eNSP 模态确认框挡住了 —— {detail}。\n"
            "实测：只要 eNSP 里开着拓扑，关闭就会弹这个框（没有拓扑时 1~5 秒干净退出）。\n"
            "请到桌面上点掉它。本工具按设计不会替你点按钮，也不会强制 taskkill。"
            f"{_leftover_note()}"
        )
    return (
        f"已向 eNSP 主窗口发送关闭请求，但 15 秒后进程（PID {pid}）仍未退出，"
        "且没有检测到可见的模态对话框。\n"
        "本工具按设计不会强制 taskkill，请到桌面上确认 eNSP 的状态。"
        f"{_leftover_note()}"
    )


def open_topology(file_path: str) -> str:
    """在 eNSP 中打开一个 .topo 拓扑（eNSP 没开的话会一并启动）。"""
    path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.isfile(path):
        return f"拓扑文件不存在: {path}"
    if not path.lower().endswith(".topo"):
        return f"不是 .topo 文件: {path}"

    exe = find_ensp_exe()
    if not exe:
        return (
            "未找到 eNSP_Client.exe，无法打开拓扑。"
            "请设置环境变量 ENSP_EXE 指向 eNSP_Client.exe 的完整路径。"
        )

    active = _active_ensp_pid()
    was_running = bool(active)
    before = _board_state(active) if was_running else None

    try:
        proc = subprocess.Popen(
            [exe, path],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            cwd=os.path.dirname(exe),
        )
    except Exception as e:
        return f"打开拓扑失败: {e}"

    # 成功判据绝不能是"画布上有设备标签" —— eNSP 里本来就开着别的拓扑时它恒为真，
    # 会静默误判成成功（2026-10-17 实测踩过）。只能看交接前后画布状态有没有变化。
    deadline = time.time() + (20 if was_running else 40)
    after = None
    while time.time() < deadline:
        time.sleep(0.5)
        active = _active_ensp_pid()
        if not active:
            continue
        after = _board_state(active)
        if after is None:
            continue
        if before is None:
            if after[0] >= 1 and sum(after[1]) > 0:
                break
        elif after[0] > before[0] or after[1] != before[1]:
            break
    else:
        if not _active_ensp_pid():
            return f"已调用 eNSP 打开 {path}，但没检测到 eNSP 进程，请到桌面确认。"
        return _open_topo_failed(path, was_running, before, after)

    started = "eNSP 原本已在运行，拓扑被转交给它" if was_running else "本次启动了 eNSP"
    return (
        f"拓扑已打开: {path}\n"
        f"（{started}；启动器进程 PID {proc.pid}）\n"
        f"交接前画布状态: {before}  ->  交接后: {after}\n"
        f"{_snapshot(active)}\n"
        "提示：载入 .topo 只是画出拓扑，设备不会自动启动，需要在 GUI 里点「启动设备」。"
    )


def _open_topo_failed(path, was_running, before, after) -> str:
    """open_topology 没成功时的回显。如实说明，不粉饰。"""
    lines = [
        f"拓扑没有打开: {path}",
        f"交接前画布状态: {before}  ->  20/40 秒后: {after}",
        "判据是画布状态有没有变化；没有变化即视为失败（不再用「有设备标签」这种绝对量判据，"
        "那会在 eNSP 已开着别的拓扑时把没打开误报成打开）。",
    ]
    if was_running:
        lines += [
            "",
            "原因：eNSP 已在运行时，命令行把 .topo 转交给它的这条路走不通（实测："
            "运行中且已有非空拓扑 -> 完全无效；运行中且一个画布都没有 -> 40 秒超时无效；"
            "只有运行中且是空白板时成功过 1 次，未复现）。",
            "可行的做法：",
            "  1) 在 eNSP 界面里用 Ctrl+O 手动打开；或",
            "  2) 先调用 close_ensp 退出 eNSP，再调用本工具 —— 这条路径实测可靠。",
            "注意如果你要打开的拓扑本来就已经打开，那么「无变化」是正常的。",
        ]
    else:
        lines += ["", "eNSP 是新启动的，但画布上没出现设备标签。"
                      "可能拓扑本身为空、渲染未完成，或打开被弹窗挡住了。请到桌面确认。"]
    return "\n".join(lines)


def close_topology() -> str:
    """关闭当前拓扑页签，eNSP 保持运行。

    注意：名字和输入没有绑定 —— eNSP 没有"关指定拓扑"的接口，本工具关的是
    面积最大的那个可见画布。
    """
    pid = _active_ensp_pid()
    if not pid:
        return "eNSP 未在运行，没有可关闭的拓扑。"

    main = _main_window(pid)
    if not main:
        return f"eNSP 进程在（PID {pid}），但没找到主框架窗口。"

    canvases = _canvas_windows(main)
    if not canvases:
        return f"eNSP 中当前没有打开的拓扑页签。\n{_snapshot(pid)}"

    target = canvases[0]
    labels_before = _count_device_labels(target)
    multi = (
        f"\n注意：当前有 {len(canvases)} 个画布，本工具只关面积最大的那个。"
        if len(canvases) > 1 else ""
    )

    _post_close(target)
    deadline = time.time() + 5
    while time.time() < deadline:
        time.sleep(0.5)
        if not _is_window(target):
            note = _leftover_note()
            return (
                f"拓扑页签已关闭（画布窗口 {target}，关闭前设备标签 {labels_before} 个）。"
                f"eNSP 仍在运行（PID {pid}）。{multi}\n{_snapshot(pid)}{note}"
            )

    return (
        f"已向拓扑画布（hwnd={target}）发送 WM_CLOSE，但 5 秒后窗口仍在。\n"
        "最常见原因：该拓扑有未保存的改动，eNSP 弹了确认框在等人点。请到桌面处理。"
        f"{_leftover_note()}"
    )
