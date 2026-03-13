"""
eNSP 协议排错工具

提供 OSPF、BGP、ACL、NAT 等协议的状态检查和排错功能。
支持结构化 JSON 输出。
"""
import logging
from typing import Optional
from netmiko import ConnectHandler
from ..config import get_device_info
from ..parsers import parse_ospf_neighbors, parse_bgp_peers, to_json

logger = logging.getLogger("ensp_mcp.protocol")


def check_ospf_neighbors(
    device_ip: str,
    process_id: Optional[int] = None,
    structured: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    检查 OSPF 邻居状态

    Args:
        structured: 为 True 时返回 JSON 格式
    """
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            results = []

            # 邻居表
            if process_id:
                cmd = f"display ospf {process_id} peer"
            else:
                cmd = "display ospf peer"
            peer_output = net_connect.send_command(cmd)

            if structured:
                parsed = parse_ospf_neighbors(peer_output)
                return to_json({"device": device_ip, "ospf_neighbors": parsed})

            results.append(f"OSPF 邻居:\n{peer_output}")

            # 接口信息
            if process_id:
                cmd = f"display ospf {process_id} interface"
            else:
                cmd = "display ospf interface"
            output = net_connect.send_command(cmd)
            results.append(f"\nOSPF 接口:\n{output}")

            # LSDB 概要
            if process_id:
                cmd = f"display ospf {process_id} lsdb"
            else:
                cmd = "display ospf lsdb"
            output = net_connect.send_command(cmd)
            results.append(f"\nOSPF LSDB:\n{output}")

            return f"[{device_ip}] OSPF 状态:\n" + "\n".join(results)
    except Exception as e:
        return f"OSPF 检查失败 [{device_ip}]: {str(e)}"


def check_bgp_neighbors(
    device_ip: str,
    address_family: str = "ipv4",
    peer_ip: Optional[str] = None,
    structured: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    检查 BGP 邻居状态

    Args:
        structured: 为 True 时返回 JSON 格式
    """
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            results = []

            # 邻居概要
            peer_output = net_connect.send_command("display bgp peer")

            if structured:
                parsed = parse_bgp_peers(peer_output)
                return to_json({"device": device_ip, "bgp_peers": parsed})

            results.append(f"BGP 邻居概要:\n{peer_output}")

            # 如果指定了对等体,获取详细信息
            if peer_ip:
                output = net_connect.send_command(
                    f"display bgp peer {peer_ip} verbose"
                )
                results.append(f"\nBGP 对等体 {peer_ip} 详细信息:\n{output}")

            # BGP 路由表
            output = net_connect.send_command("display bgp routing-table")
            results.append(f"\nBGP 路由表:\n{output}")

            return f"[{device_ip}] BGP 状态:\n" + "\n".join(results)
    except Exception as e:
        return f"BGP 检查失败 [{device_ip}]: {str(e)}"


def check_acl(
    device_ip: str,
    acl_number: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """检查 ACL 规则和匹配统计"""
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            if acl_number:
                command = f"display acl {acl_number}"
            else:
                command = "display acl all"

            output = net_connect.send_command(command)
            return f"[{device_ip}] ACL 规则:\n{output}"
    except Exception as e:
        return f"ACL 检查失败 [{device_ip}]: {str(e)}"


def check_nat(
    device_ip: str,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """检查 NAT 状态和会话信息"""
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            results = []

            output = net_connect.send_command("display nat session all")
            results.append(f"NAT 会话表:\n{output}")

            output = net_connect.send_command("display nat address-group")
            results.append(f"\nNAT 地址池:\n{output}")

            output = net_connect.send_command("display nat statistics")
            results.append(f"\nNAT 统计:\n{output}")

            return f"[{device_ip}] NAT 状态:\n" + "\n".join(results)
    except Exception as e:
        return f"NAT 检查失败 [{device_ip}]: {str(e)}"


def check_stp(
    device_ip: str,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """检查 STP/RSTP/MSTP 生成树状态"""
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            results = []

            output = net_connect.send_command("display stp brief")
            results.append(f"STP 摘要:\n{output}")

            output = net_connect.send_command("display stp")
            results.append(f"\nSTP 详细信息:\n{output}")

            return f"[{device_ip}] STP 状态:\n" + "\n".join(results)
    except Exception as e:
        return f"STP 检查失败 [{device_ip}]: {str(e)}"


def check_vlan(
    device_ip: str,
    vlan_id: Optional[int] = None,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """检查 VLAN 配置和端口分配"""
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            if vlan_id:
                command = f"display vlan {vlan_id}"
            else:
                command = "display vlan"

            output = net_connect.send_command(command)
            return f"[{device_ip}] VLAN 信息:\n{output}"
    except Exception as e:
        return f"VLAN 检查失败 [{device_ip}]: {str(e)}"
