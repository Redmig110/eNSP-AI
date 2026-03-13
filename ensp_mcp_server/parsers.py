"""
eNSP 输出解析器

将华为设备 CLI 输出解析为结构化 JSON 数据。
"""
import json
import re
from typing import Optional


def parse_interface_brief(output: str) -> list[dict]:
    """
    解析 display ip interface brief 输出

    Returns:
        [{"interface": "GE0/0/0", "ip": "10.0.0.1", "mask": "24", "physical": "up", "protocol": "up"}, ...]
    """
    results = []
    # 跳过表头，匹配数据行
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Interface") or line.startswith("-"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            entry = {"interface": parts[0]}
            if len(parts) >= 2:
                entry["ip"] = parts[1]
            if len(parts) >= 3:
                entry["mask"] = parts[2]
            if len(parts) >= 4:
                entry["physical"] = parts[3]
            if len(parts) >= 5:
                entry["protocol"] = parts[4]
            results.append(entry)
    return results


def parse_routing_table(output: str) -> list[dict]:
    """
    解析 display ip routing-table 输出

    Returns:
        [{"destination": "10.0.0.0/24", "protocol": "OSPF", "preference": "10",
          "cost": "1", "nexthop": "10.0.1.1", "interface": "GE0/0/0"}, ...]
    """
    results = []
    # 华为路由表格式: Destination/Mask Proto Pre Cost Flags NextHop Interface
    route_pattern = re.compile(
        r'(\d+\.\d+\.\d+\.\d+/\d+)\s+'
        r'(\S+)\s+'
        r'(\d+)\s+'
        r'(\d+)\s+'
        r'(\S*)\s+'
        r'(\d+\.\d+\.\d+\.\d+)\s+'
        r'(\S+)'
    )
    for line in output.splitlines():
        m = route_pattern.search(line)
        if m:
            results.append({
                "destination": m.group(1),
                "protocol": m.group(2),
                "preference": m.group(3),
                "cost": m.group(4),
                "nexthop": m.group(6),
                "interface": m.group(7),
            })
    return results


def parse_ospf_neighbors(output: str) -> list[dict]:
    """
    解析 display ospf peer 输出

    Returns:
        [{"router_id": "1.1.1.1", "address": "10.0.0.1", "state": "Full",
          "priority": "1", "dead_time": "00:00:35"}, ...]
    """
    results = []
    # 匹配 OSPF 邻居行
    current = {}
    for line in output.splitlines():
        line = line.strip()
        if "Router ID" in line:
            if current:
                results.append(current)
            rid_match = re.search(r'Router ID:\s*(\S+)', line)
            current = {"router_id": rid_match.group(1) if rid_match else ""}
        elif "Address" in line and ":" in line:
            addr_match = re.search(r'Address:\s*(\S+)', line)
            if addr_match and current:
                current["address"] = addr_match.group(1)
        elif "State" in line and ":" in line and current:
            state_match = re.search(r'State:\s*<(\S+)>', line)
            if not state_match:
                state_match = re.search(r'State:\s*(\S+)', line)
            if state_match:
                current["state"] = state_match.group(1)
        elif "Priority" in line and ":" in line and current:
            pri_match = re.search(r'Priority:\s*(\d+)', line)
            if pri_match:
                current["priority"] = pri_match.group(1)
        elif "Dead timer" in line and current:
            dead_match = re.search(r'Dead timer.*?(\d+:\d+:\d+)', line)
            if dead_match:
                current["dead_time"] = dead_match.group(1)

    if current and current.get("router_id"):
        results.append(current)
    return results


def parse_arp_table(output: str) -> list[dict]:
    """
    解析 display arp 输出

    Returns:
        [{"ip": "10.0.0.1", "mac": "0000-0000-0001", "expire": "20",
          "type": "D", "interface": "GE0/0/0", "vlan": ""}, ...]
    """
    results = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("IP Address") or line.startswith("-"):
            continue
        parts = line.split()
        if len(parts) >= 4 and re.match(r'\d+\.\d+\.\d+\.\d+', parts[0]):
            entry = {
                "ip": parts[0],
                "mac": parts[1] if len(parts) > 1 else "",
                "expire": parts[2] if len(parts) > 2 else "",
                "type": parts[3] if len(parts) > 3 else "",
                "interface": parts[4] if len(parts) > 4 else "",
                "vlan": parts[5] if len(parts) > 5 else "",
            }
            results.append(entry)
    return results


def parse_bgp_peers(output: str) -> list[dict]:
    """
    解析 display bgp peer 输出

    Returns:
        [{"peer": "10.0.0.2", "as": "65001", "msg_rcvd": "100",
          "msg_sent": "100", "state": "Established"}, ...]
    """
    results = []
    for line in output.splitlines():
        line = line.strip()
        # 匹配 IP 开头的行
        if re.match(r'\d+\.\d+\.\d+\.\d+', line):
            parts = line.split()
            if len(parts) >= 3:
                entry = {"peer": parts[0]}
                # BGP peer 表格列数可能不同
                if len(parts) >= 2:
                    entry["version"] = parts[1]
                if len(parts) >= 3:
                    entry["as"] = parts[2]
                if len(parts) >= 4:
                    entry["msg_rcvd"] = parts[3]
                if len(parts) >= 5:
                    entry["msg_sent"] = parts[4]
                # 最后一列通常是 State
                entry["state"] = parts[-1]
                results.append(entry)
    return results


def to_json(data) -> str:
    """将解析结果转为格式化 JSON 字符串"""
    return json.dumps(data, ensure_ascii=False, indent=2)
