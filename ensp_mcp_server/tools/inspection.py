"""
eNSP 巡检 / 实验验收工具

两个工具：
  verify_expectations —— 跑一组「命令 + 预期」，逐条判定 PASS / FAIL
  inspect_topology    —— 不给预期，做通用体检（可达性 / 接口 / 日志 / 是否已保存）

判定逻辑 evaluate_check() 是纯函数，不碰网络，可以直接单测。
"""
import json
import logging
import re
from collections import Counter
from typing import Optional

from ..config import _device_registry
from .cli import _connect, _cli_rejected

logger = logging.getLogger("ensp_mcp.inspection")

# ==================== 判定结果 ====================

PASS = "PASS"      # 写了预期，全部满足
FAIL = "FAIL"      # 写了预期，有没满足的
INFO = "INFO"      # 一条预期都没写，只采集回显不判定
ERROR = "ERROR"    # 连不上 / 命令被拒 / 清单本身有问题

# FAIL / INFO 的回显最多贴这么多字符，避免刷屏
_OUTPUT_CLIP = 2000

# 配置差异最多列几行
_SAVE_DIFF_SHOWN = 6

# VRP 日志行形如 `... AR1 %%01IFNET/4/LINK_STATE(l)[0]:...`，
# 斜杠后的数字是严重级别：0 emergency / 1 alert / 2 critical / 3 error / 4 warning。
# 只取 0-3 太宽会刷屏，收 0-3 又会漏掉真正有用的事件，所以：
#   级别 0-3 一律报；级别 4 只在带下面这些坏消息关键词时才报。
_LOG_SEV_RE = re.compile(r"%%\d\d[A-Z0-9_]+/[0-3]/")
_LOG_BAD_RE = re.compile(r"Error|Failed|Failure|DUPLICATE", re.I)

# 启动期必然产生的噪声。实测一台刚起来的 AR 会刷满 LINK_STATE / IF_STATE，
# 而 `DEV/4/ENTUP ... register success` 更是**成功**消息却带着级别 4。
# 接口状态由上面的「接口」那一节单独看，这里不重复报。
_LOG_NOISE_RE = re.compile(r"LINK_STATE|IF_STATE|ENTITYTRAP|register success", re.I)


def _log_is_interesting(line: str) -> bool:
    if _LOG_NOISE_RE.search(line):
        return False
    return bool(_LOG_SEV_RE.search(line) or _LOG_BAD_RE.search(line))

# display ip interface brief 的数据行：接口名  IP  物理状态  协议状态
_IP_INTF_RE = re.compile(
    r"^\s*(\S+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\S+)\s+(\S+)\s*$", re.MULTILINE
)


# ==================== 纯判定逻辑 ====================

def evaluate_check(check: dict, output: str) -> dict:
    """
    拿一条检查的定义和设备的原始回显做判定。不碰网络，纯函数。

    返回 {"status": PASS|FAIL|INFO, "problems": [人类可读的问题描述, ...]}
    """
    contains = check.get("expect_contains") or []
    not_contains = check.get("expect_not_contains") or []
    pattern = check.get("expect_match")

    # 一条预期都没写 —— 只采集，不判定
    if not contains and not not_contains and not pattern:
        return {"status": INFO, "problems": []}

    problems = []
    for s in contains:
        if s not in output:
            problems.append(f"缺少: {s}")
    for s in not_contains:
        if s in output:
            problems.append(f"不应出现: {s}")
    if pattern:
        try:
            if not re.search(pattern, output):
                problems.append(f"未匹配正则: {pattern}")
        except re.error as e:
            problems.append(f"正则写法有误: {pattern}（{e}）")

    return {"status": FAIL if problems else PASS, "problems": problems}


def _clip(text: str, limit: int = _OUTPUT_CLIP) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n…（已截断，原文共 {len(text)} 字符）"


def _looks_registered(device: str) -> bool:
    """注册表里的名字不区分大小写（_resolve_device 也是这么找的）。"""
    key = (device or "").lower()
    return any(name.lower() == key for name in _device_registry)


def _connect_error(device: str, err: Exception) -> str:
    """
    把连不上的原因说具体。最常见的就是名字没注册 —— 这时
    _resolve_device 会把它当成主机名去解析，报一句没头没脑的
    getaddrinfo failed，对排错毫无帮助。
    """
    if not _looks_registered(device):
        known = ", ".join(_device_registry) or "无"
        return (f"设备 '{device}' 不在注册表里，已注册的有: {known}。"
                f"先 register_device 或 auto_discover")
    return f"连接或执行失败: {err}"


# ==================== 清单加载 ====================

def _read_checklist_file(path: str) -> list:
    """读 JSON 清单。接受裸数组，或 {"checks": [...]}。"""
    # utf-8-sig：Windows 记事本存 UTF-8 会带 BOM，直接 json.load 会报错
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if isinstance(data, dict):
        if "checks" not in data:
            raise ValueError('清单是对象但没有 "checks" 字段')
        data = data["checks"]
    if not isinstance(data, list):
        raise ValueError("清单顶层必须是数组，或 {\"checks\": [...]}")
    return data


def _load_checks(inline: Optional[list], checklist_file: Optional[str]) -> list:
    """合并两个来源的检查：文件在前，内联在后（内联的通常是想临时补的）。"""
    checks = []
    if checklist_file:
        checks.extend(_read_checklist_file(checklist_file))
    if inline:
        if not isinstance(inline, list):
            raise ValueError("checks 必须是数组")
        checks.extend(inline)

    for i, ch in enumerate(checks, 1):
        if not isinstance(ch, dict):
            raise ValueError(f"第 {i} 条检查不是对象")
        if not ch.get("device") or not ch.get("command"):
            raise ValueError(f"第 {i} 条检查缺少 device 或 command 字段")
    return checks


# ==================== 工具 1：实验验收 ====================

def verify_expectations(
    checks: Optional[list] = None,
    checklist_file: Optional[str] = None,
) -> str:
    """
    跑一组检查，逐条判定。

    每条检查：{"device": "LSW1", "command": "display vrrp",
              "expect_contains": [...], "expect_not_contains": [...],
              "expect_match": "正则", "desc": "这条在验什么"}
    """
    try:
        checks = _load_checks(checks, checklist_file)
    except Exception as e:
        return f"清单加载失败: {e}"

    if not checks:
        return "没有要执行的检查：请给 checks 或 checklist_file。"

    results = []
    for i, ch in enumerate(checks, 1):
        device, command = ch["device"], ch["command"]
        label = ch.get("desc") or command

        # 每条检查独立建连，一条挂了不影响后面的
        try:
            with _connect(device) as c:
                output = c.send_command(command, timeout=30)
        except Exception as e:
            results.append({
                "index": i, "device": device, "label": label,
                "status": ERROR, "problems": [_connect_error(device, e)], "output": "",
            })
            continue

        if _cli_rejected(output):
            results.append({
                "index": i, "device": device, "label": label,
                "status": ERROR, "problems": ["设备拒绝了这条命令"],
                "output": output,
            })
            continue

        verdict = evaluate_check(ch, output)
        results.append({
            "index": i, "device": device, "label": label,
            "status": verdict["status"], "problems": verdict["problems"],
            "output": output,
        })

    return _format_verify_report(results)


def _format_verify_report(results: list) -> str:
    counts = Counter(r["status"] for r in results)
    parts = [f"{counts[s]} 通过" for s in (PASS,) if counts[s]]
    parts += [f"{counts[s]} 失败" for s in (FAIL,) if counts[s]]
    parts += [f"{counts[s]} 采集" for s in (INFO,) if counts[s]]
    parts += [f"{counts[s]} 错误" for s in (ERROR,) if counts[s]]

    out = [f"[验收] {len(results)} 条检查 — {' / '.join(parts)}", "─" * 52]

    for r in results:
        out.append(f"[{r['status']:<5}] {r['device']:<8} {r['label']}")
        for p in r["problems"]:
            out.append(f"          {p}")
        # 失败和采集才贴回显 —— 通过的没什么可看的，贴了只会淹掉重点
        if r["status"] in (FAIL, INFO, ERROR) and r["output"]:
            out.append("          ── 原始回显 ──")
            out.extend(f"          {ln}" for ln in _clip(r["output"]).splitlines())
        out.append("")

    return "\n".join(out).rstrip()


# ==================== 工具 2：通用巡检 ====================

def inspect_topology(device_names: Optional[list] = None) -> str:
    """
    不给预期，对每台设备做通用体检：
      1. 可达性 —— 看 display 是否真的执行了且被接受（health_check 会误报 OK）
      2. 有 IP 但协议 down 的接口
      3. 最近日志里值得看的行
      4. 当前配置与已保存配置是否一致（软提示，不判 FAIL）
    """
    if not device_names:
        device_names = list(_device_registry.keys())
    if not device_names:
        return "没有注册任何设备，请先 register_device 或 auto_discover。"

    findings = [_inspect_one(name) for name in device_names]
    return _format_inspect_report(findings)


def _inspect_one(device: str) -> dict:
    """体检单台设备。任何一步失败都只记在结果里，不往外抛。"""
    f = {
        "device": device, "reachable": False, "error": "",
        "down_interfaces": [], "log_hits": [], "save_state": "unknown",
        "save_diff": [],
    }

    # 1. 可达性：必须以「display 真的跑出结果且没被拒」为准
    try:
        with _connect(device, timeout=20) as c:
            out = c.send_command("display version", timeout=20)
    except Exception as e:
        f["error"] = _connect_error(device, e)
        return f

    if _cli_rejected(out):
        f["error"] = "连上了，但设备不接受 display version（可能还在启动）"
        return f
    f["reachable"] = True

    # 2. 有 IP 但没起来的接口。这条命令只列已配 IP 的接口，
    #    所以出现的 down 都是真问题，没有「未使用端口」的噪声。
    try:
        with _connect(device, timeout=20) as c:
            brief = c.send_command("display ip interface brief", timeout=20)
        if not _cli_rejected(brief):
            for name, ip, phys, proto in _IP_INTF_RE.findall(brief):
                if "down" in phys.lower() or "down" in proto.lower():
                    f["down_interfaces"].append(f"{name} ({ip}) 物理={phys} 协议={proto}")
    except Exception as e:
        f["down_interfaces"].append(f"（读取接口状态失败: {e}）")

    # 3. 日志
    try:
        with _connect(device, timeout=20) as c:
            logs = c.send_command("display logbuffer", timeout=20)
        if not _cli_rejected(logs):
            f["log_hits"] = [
                ln.strip() for ln in logs.splitlines()
                if _log_is_interesting(ln) and not ln.lstrip().startswith("Logging buffer")
            ][-5:]
    except Exception:
        pass

    # 4. 是否已保存。**只给证据，不下结论。**
    #    这两个回显本来就不完全等价：AR 上 `flash:/portalpage.zip` 与
    #    `portalpage.zip` 是同一条配置的两种写法，`wlan ac-global ...`
    #    是模块自动生成的。只报「一致 / 有 N 行差异」会在这类设备上
    #    长期假警报 —— 所以把差异行本身列出来，让人一眼判断。
    try:
        with _connect(device, timeout=30) as c:
            current = c.send_command("display current-configuration", timeout=30)
            saved = c.send_command("display saved-configuration", timeout=30)
        if _cli_rejected(saved) or not saved.strip():
            f["save_state"] = "never"      # 从没保存过
        else:
            cur, sav = _normalize_config(current), _normalize_config(saved)
            if cur == sav:
                f["save_state"] = "consistent"
            else:
                f["save_state"] = "dirty"
                f["save_diff"] = (
                    [f"+ {x}" for x in sorted(cur - sav)]
                    + [f"- {x}" for x in sorted(sav - cur)]
                )
    except Exception:
        f["save_state"] = "unknown"

    return f


def _normalize_config(text: str) -> set:
    """把配置回显归一化成可比的行集合：丢掉分隔符和空行。"""
    lines = set()
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or ln.startswith("!") or ln.startswith("return"):
            continue
        lines.add(ln)
    return lines


def _format_inspect_report(findings: list) -> str:
    reachable = sum(1 for f in findings if f["reachable"])
    out = [
        f"[巡检] {len(findings)} 台设备 — {reachable} 可达 / {len(findings) - reachable} 不可达",
        "─" * 52,
    ]

    for f in findings:
        if not f["reachable"]:
            out += [f"{f['device']}  ✗ 不可达", f"          {f['error']}", ""]
            continue

        out.append(f"{f['device']}  ✓ 可达")

        out.append("    接口: " + (
            "未发现「有 IP 但协议 down」的接口"
            if not f["down_interfaces"]
            else f"⚠ {len(f['down_interfaces'])} 个异常 —— " + "; ".join(f["down_interfaces"])
        ))

        out.append("    日志: " + (
            "最近没有值得注意的记录"
            if not f["log_hits"]
            else f"⚠ {len(f['log_hits'])} 条值得看"
        ))
        for ln in f["log_hits"]:
            out.append(f"        {ln}")

        out.append("    配置: " + {
            "consistent": "当前配置与已保存配置一致",
            "dirty": f"⚠ 与已保存配置有 {len(f['save_diff'])} 行差异 —— "
                     f"可能是改完没 save，也可能只是回显写法不同，自行判断",
            "never": "⚠ 从未保存过配置（display saved-configuration 无内容）",
            "unknown": "无法判断（读取失败或设备不支持）",
        }[f["save_state"]])
        for ln in f["save_diff"][:_SAVE_DIFF_SHOWN]:
            out.append(f"        {ln}")
        if len(f["save_diff"]) > _SAVE_DIFF_SHOWN:
            out.append(f"        …还有 {len(f['save_diff']) - _SAVE_DIFF_SHOWN} 行")

        out.append("")

    return "\n".join(out).rstrip()
