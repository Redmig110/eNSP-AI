"""
eNSP 拓扑发现工具

解析 eNSP .topo XML 文件，提取设备、接口和链路信息。

关于 .topo 文件的两个坑（都踩过）：
1. 编码。文件头写的是 encoding="UNICODE"，但这是**假的** —— eNSP 实际写的是
   GBK(ANSI)。直接把路径交给 ET.parse() 会抛 LookupError: unknown encoding: UNICODE。
   所以这里自己读字节、自己判编码，再把它那条假声明换掉。
2. 标签名。真实结构是 devices/dev、lines/line/interfacePair、txttips/txttip，
   不是 <device>/<node>。前端 topo-viewer.html 里的解析器是同一套 schema。
"""
import logging
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Optional

logger = logging.getLogger("ensp_mcp.topology")

# 默认搜索的最大递归深度。eNSP 会在每个 .topo 旁边放一个以设备 UUID 命名的目录，
# 深度限制要能跳过它们，又不能把 examples/<拓扑名>/x.topo 这种挡在外面。
_MAX_DEPTH = 4
# 列出的文件条数上限，超出只报总数
_MAX_LIST = 60

_DEV_PREFIX = ("S", "CE", "LS", "QS")


def discover_topology(file_path: str) -> str:
    """
    解析 eNSP .topo 文件，提取拓扑信息

    Args:
        file_path: .topo 文件的完整路径

    Returns:
        拓扑描述（设备列表、接口、链路关系、文本标注）
    """
    if not os.path.exists(file_path):
        return f"文件不存在: {file_path}"

    if not file_path.lower().endswith(".topo"):
        return f"不是 .topo 文件: {file_path}"

    try:
        raw = open(file_path, "rb").read()
        root = ET.fromstring(_loadable_xml(raw))
    except ET.ParseError as e:
        return f"XML 解析失败: {e}"
    except (LookupError, ValueError, OSError) as e:
        # LookupError: 声明里出现了 Python 不认识的编码名
        return f"文件读取/解析失败: {e}"

    devices = [_extract_device_info(el) for el in root.findall("devices/dev")]
    devices = [d for d in devices if d]

    links = []
    for line in root.findall("lines/line"):
        links.extend(_extract_line_links(line, devices))

    tips = []
    for el in root.findall("txttips/txttip"):
        content = _one_line(el.get("content") or "")
        if content:
            tips.append(content)

    return _format_report(file_path, root, devices, links, tips)


def find_topo_files(search_dir: str = "") -> str:
    """
    搜索 .topo 文件

    Args:
        search_dir: 搜索目录，为空则搜索常见位置

    Returns:
        找到的 .topo 文件列表
    """
    roots = [search_dir] if search_dir else _default_roots()

    found, seen = [], set()
    for d in roots:
        if not os.path.isdir(d):
            continue
        for path in _walk_topos(d):
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                seen.add(key)
                found.append(path)

    if not found:
        return "未找到 .topo 文件。请指定 search_dir 参数。"

    lines = [f"找到 {len(found)} 个 .topo 文件:"]
    for f in found[:_MAX_LIST]:
        lines.append(f"  {f}")
    if len(found) > _MAX_LIST:
        lines.append(f"  ... 还有 {len(found) - _MAX_LIST} 个")
    return "\n".join(lines)


# --------------------------------------------------------------------- 解析层


def _loadable_xml(raw: bytes) -> str:
    """把 .topo 字节流变成 ET.fromstring 能吃的东西"""
    text = _decode_topo(raw)
    # eNSP 的 encoding="UNICODE" 不是合法编码名，留着会让解析器去查一个不存在的
    # codec。编码我们已经自己解完了，声明换掉即可（前端 topo-viewer.html 同样处理）。
    return re.sub(r"<\?xml[^?]*\?>", '<?xml version="1.0"?>', text, count=1)


def _decode_topo(raw: bytes) -> str:
    """按 BOM / UTF-8 / GBK 顺序判定编码，永不抛异常"""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw[2:].decode("utf-16", errors="replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace")
    # UTF-16 无 BOM：ASCII 内容会呈现为 \x00 交替
    if len(raw) >= 4 and raw[1] == 0 and raw[3] == 0:
        return raw.decode("utf-16-le", errors="replace")

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # eNSP 中文环境写出来的就是 GBK
        try:
            return raw.decode("gbk")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")


def _one_line(text: str) -> str:
    """标注里可以带换行（eNSP 存的是 &#x0D;&#x0A; 实体，XML 解析后是真换行），
    直接回显会把报告的行结构冲散。压成一行。"""
    return re.sub(r"\s*[\r\n]+\s*", " / ", text).strip()


def _is_switch(model: str) -> bool:
    """交换机接口从 0/0/1 起编号，路由器从 0/0/0 起 —— 与 topo-viewer.html 一致"""
    m = (model or "").upper()
    return bool(re.match(r"^(%s)\d" % "|".join(_DEV_PREFIX), m))


def _extract_device_info(elem: ET.Element) -> Optional[dict]:
    """从 <dev> 提取设备信息"""
    name = elem.get("name") or elem.get("id")
    if not name:
        return None

    blocks = []
    for slot in elem.findall("slot"):
        for iface in slot.findall("interface"):
            try:
                count = int(iface.get("count") or 0)
            except ValueError:
                count = 0
            blocks.append({
                "type": iface.get("sztype") or "",
                "name": iface.get("interfacename") or iface.get("sztype") or "if",
                "count": count,
            })

    model = elem.get("model") or ""
    return {
        "id": elem.get("id") or "",
        "name": name,
        "model": model,
        "com_port": elem.get("com_port") or "",
        "interfaces": _iface_names(model, blocks),
    }


def _iface_names(model: str, blocks: list[dict]) -> list[str]:
    """把 slot/interface 的 count 展开成接口名。

    序号按**类型**分别累计，跨块连续。不能每块从 base 重新数：AR2220 的块是
    GE×1 + GE×2，那样会数出 GE0/0/0, GE0/0/0, GE0/0/1（重复且丢端口），
    而 .topo 里 AR2220 的 srcIndex 实际用到 0,1,2 三个不同端口，
    正确结果是 GE0/0/0, GE0/0/1, GE0/0/2。

    srcIndex 是设备端口列表上的**全局**下标（.topo 里只有一个标量，没有类型信息），
    所以按块顺序走；名字里的编号则按前缀各自计数，这样
    AR1220 的 GE×2 + Ethernet×8 + Serial×2 会得到
    GE0/0/0-1、Eth0/0/0-7、Serial0/0/0-1。

    交换机从 0/0/1 起编号，其余从 0/0/0 起（与 topo-viewer.html 一致）。
    """
    base = 1 if _is_switch(model) else 0
    counters: dict[str, int] = {}
    names = []
    for blk in blocks:
        prefix = "Eth" if blk["name"] == "Ethernet" else blk["name"]
        for _ in range(blk["count"]):
            n = counters.get(prefix, base)
            names.append(f"{prefix}0/0/{n}")
            counters[prefix] = n + 1
    return names


def _extract_line_links(line: ET.Element, devices: list[dict]) -> list[dict]:
    """一条 <line> 可以挂多个 <interfacePair>（多段链路），全部取出"""
    by_id = {d["id"]: d for d in devices}
    src, dst = by_id.get(line.get("srcDeviceID")), by_id.get(line.get("destDeviceID"))

    links = []
    for pair in line.findall("interfacePair"):
        links.append({
            "src_device": src["name"] if src else line.get("srcDeviceID", "?"),
            "src_port": _port_at(src, pair.get("srcIndex")),
            "dst_device": dst["name"] if dst else line.get("destDeviceID", "?"),
            "dst_port": _port_at(dst, pair.get("tarIndex")),
            "type": pair.get("lineName") or "Copper",
        })
    return links


def _port_at(dev: Optional[dict], index) -> str:
    """把 .topo 里的全局接口索引还原成接口名"""
    if dev is None:
        return f"if{index}"
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return f"if{index}"
    if 0 <= idx < len(dev["interfaces"]):
        return dev["interfaces"][idx]
    return f"if{idx}"


# --------------------------------------------------------------------- 输出层


def _disp_width(s: str) -> int:
    """终端显示宽度（CJK 占两格），用于对齐含中文的设备名"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(1, width - _disp_width(s))


def _compress(names: list[str]) -> str:
    """GE0/0/1..GE0/0/24 -> 'GE0/0/1-GE0/0/24 (24)'"""
    if not names:
        return "-"
    if len(names) == 1:
        return names[0]

    parts = [re.match(r"^(.*?)(\d+)$", n) for n in names]
    if all(parts) and len({p.group(1) for p in parts}) == 1:
        nums = [int(p.group(2)) for p in parts]
        if nums == list(range(nums[0], nums[0] + len(nums))):
            prefix = parts[0].group(1)
            return f"{prefix}{nums[0]}-{prefix}{nums[-1]} ({len(names)})"

    if len(names) <= 8:
        return ", ".join(names)
    return ", ".join(names[:8]) + f", ... ({len(names)} 个)"


def _format_report(file_path, root, devices, links, tips) -> str:
    lines = [f"拓扑文件: {os.path.basename(file_path)}"]
    version = root.get("version")
    if version:
        lines.append(f"拓扑版本: {version}")
    lines.append(f"设备数量: {len(devices)}")
    lines.append(f"链路数量: {len(links)}")
    lines.append(f"文本标注数量: {len(tips)}")

    if devices:
        lines.append("\n=== 设备列表 ===")
        head = ["名称", "型号", "控制台端口", "接口"]
        lines.append("".join([_pad(h, 20 if i < 2 else 12) for i, h in enumerate(head)]))
        lines.append("-" * 70)
        for d in devices:
            lines.append(
                _pad(d["name"], 20)
                + _pad(d["model"] or "-", 12)
                + _pad(d["com_port"] or "-", 12)
                + _compress(d["interfaces"])
            )

    if links:
        lines.append("\n=== 链路连接 ===")
        for l in links:
            lines.append(
                f"  {l['src_device']}:{l['src_port']}  ←→  "
                f"{l['dst_device']}:{l['dst_port']}  ({l['type']})"
            )

    if tips:
        lines.append("\n=== 文本标注 ===")
        for t in tips:
            lines.append(f"  {t}")

    return "\n".join(lines)


# --------------------------------------------------------------------- 搜索层


def _default_roots() -> list[str]:
    """用户目录优先，其次从注册表里问出 eNSP 装在哪（本来就不该硬编码盘符）"""
    home = os.path.expanduser("~")
    roots = [
        os.path.join(home, "Desktop"),
        os.path.join(home, "Documents"),
        os.path.join(home, "Documents", "eNSP"),
        os.path.join(home, "Downloads"),
    ]
    roots.extend(_ensp_install_roots())
    return roots


def _ensp_install_roots() -> list[str]:
    """.topo 的文件关联指向 eNSP_Client.exe，从注册表反推安装目录"""
    roots = []
    try:
        import winreg
    except ImportError:
        return roots

    for sub in (r"topofile\shell\open\command", r"topofile\DefaultIcon"):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, sub) as key:
                value = str(winreg.QueryValueEx(key, "")[0])
        except OSError:
            continue
        m = re.search(r'"([^"]+\.exe)"|^(\S+\.exe)', value, re.IGNORECASE)
        if m:
            exe = m.group(1) or m.group(2)
            roots.append(os.path.dirname(exe))
            break

    # 注册表查不到就退回常见安装位置（两个 Program Files 都试）
    for drive in "CDEFG":
        roots.append(rf"{drive}:\Program Files\Huawei\eNSP")
    return roots


def _walk_topos(root: str):
    """在 root 下搜集 .topo。

    深度限制必须用「剪枝」而不是 break —— break 终止的是整个 os.walk，
    只要第一个分支里有深目录，后面的兄弟目录就全丢（之前只找到 1/3 就是这个原因）。
    """
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        dirnames[:] = [d for d in dirnames if depth + 1 <= _MAX_DEPTH]
        for f in filenames:
            if f.lower().endswith(".topo"):
                yield os.path.join(dirpath, f)
