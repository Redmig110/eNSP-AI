"""
eNSP 拓扑发现工具

解析 eNSP .topo XML 文件，提取设备、接口和链路信息。
"""
import logging
import os
import xml.etree.ElementTree as ET
from typing import Optional

logger = logging.getLogger("ensp_mcp.topology")


def discover_topology(file_path: str) -> str:
    """
    解析 eNSP .topo 文件，提取拓扑信息

    Args:
        file_path: .topo 文件的完整路径

    Returns:
        拓扑描述（设备列表、接口、链路关系）
    """
    if not os.path.exists(file_path):
        return f"文件不存在: {file_path}"

    if not file_path.lower().endswith(".topo"):
        return f"不是 .topo 文件: {file_path}"

    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
    except ET.ParseError as e:
        return f"XML 解析失败: {e}"

    devices = []
    links = []

    # 灵活查找设备节点（兼容不同 eNSP 版本）
    device_nodes = _find_elements(root, ["devices/device", "device", "node", "nodes/node"])
    for dev in device_nodes:
        info = _extract_device_info(dev)
        if info:
            devices.append(info)

    # 灵活查找链路节点
    link_nodes = _find_elements(root, ["links/link", "link", "connection", "connections/connection"])
    for link in link_nodes:
        info = _extract_link_info(link)
        if info:
            links.append(info)

    # 生成报告
    lines = [f"拓扑文件: {os.path.basename(file_path)}"]
    lines.append(f"设备数量: {len(devices)}")
    lines.append(f"链路数量: {len(links)}")

    if devices:
        lines.append("\n=== 设备列表 ===")
        lines.append(f"{'名称':<15} {'类型':<12} {'型号':<20}")
        lines.append("-" * 47)
        for d in devices:
            lines.append(f"{d['name']:<15} {d['type']:<12} {d['model']:<20}")
            if d.get("interfaces"):
                for iface in d["interfaces"]:
                    lines.append(f"  └─ {iface}")

    if links:
        lines.append("\n=== 链路连接 ===")
        for l in links:
            lines.append(f"  {l['src_device']}:{l['src_port']} ←→ {l['dst_device']}:{l['dst_port']}")

    return "\n".join(lines)


def find_topo_files(search_dir: str = "") -> str:
    """
    搜索 .topo 文件

    Args:
        search_dir: 搜索目录，为空则搜索常见位置

    Returns:
        找到的 .topo 文件列表
    """
    search_dirs = []
    if search_dir:
        search_dirs.append(search_dir)
    else:
        # 常见 eNSP 项目目录
        home = os.path.expanduser("~")
        search_dirs.extend([
            os.path.join(home, "Desktop"),
            os.path.join(home, "Documents"),
            os.path.join(home, "Documents", "eNSP"),
            r"C:\Program Files\Huawei\eNSP",
            r"C:\eNSP",
        ])

    found = []
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for dirpath, _, filenames in os.walk(d):
            for f in filenames:
                if f.lower().endswith(".topo"):
                    found.append(os.path.join(dirpath, f))
            # 限制递归深度为 3 层
            depth = dirpath[len(d):].count(os.sep)
            if depth >= 3:
                break

    if not found:
        return "未找到 .topo 文件。请指定 search_dir 参数。"

    lines = [f"找到 {len(found)} 个 .topo 文件:"]
    for f in found[:20]:
        lines.append(f"  {f}")
    if len(found) > 20:
        lines.append(f"  ... 还有 {len(found) - 20} 个")
    return "\n".join(lines)


def _find_elements(root: ET.Element, paths: list[str]) -> list[ET.Element]:
    """尝试多种路径查找元素"""
    for path in paths:
        elements = root.findall(path)
        if elements:
            return elements
    # 也尝试递归查找
    tag = paths[-1].split("/")[-1] if paths else "device"
    return list(root.iter(tag))


def _extract_device_info(elem: ET.Element) -> dict | None:
    """从 XML 元素提取设备信息"""
    name = (
        elem.get("name")
        or elem.findtext("name")
        or elem.get("id")
        or elem.findtext("id")
    )
    if not name:
        return None

    dev_type = (
        elem.get("type")
        or elem.findtext("type")
        or elem.get("deviceType")
        or elem.findtext("deviceType")
        or "unknown"
    )

    model = (
        elem.get("model")
        or elem.findtext("model")
        or elem.get("deviceModel")
        or elem.findtext("deviceModel")
        or ""
    )

    # 提取接口列表
    interfaces = []
    for iface_tag in ["interface", "port", "interfaces/interface", "ports/port"]:
        for iface in elem.findall(iface_tag):
            iname = iface.get("name") or iface.findtext("name") or iface.text
            if iname:
                interfaces.append(iname.strip())
        if interfaces:
            break

    return {
        "name": name,
        "type": dev_type,
        "model": model,
        "interfaces": interfaces,
    }


def _extract_link_info(elem: ET.Element) -> dict | None:
    """从 XML 元素提取链路信息"""
    # 尝试多种属性/子元素格式
    src_device = (
        elem.get("srcDevice") or elem.get("src_device")
        or elem.findtext("srcDevice") or elem.findtext("source/device")
        or ""
    )
    src_port = (
        elem.get("srcPort") or elem.get("src_port")
        or elem.findtext("srcPort") or elem.findtext("source/port")
        or ""
    )
    dst_device = (
        elem.get("dstDevice") or elem.get("dst_device")
        or elem.findtext("dstDevice") or elem.findtext("destination/device")
        or ""
    )
    dst_port = (
        elem.get("dstPort") or elem.get("dst_port")
        or elem.findtext("dstPort") or elem.findtext("destination/port")
        or ""
    )

    if not (src_device and dst_device):
        return None

    return {
        "src_device": src_device,
        "src_port": src_port,
        "dst_device": dst_device,
        "dst_port": dst_port,
    }
