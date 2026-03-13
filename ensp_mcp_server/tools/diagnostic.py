"""
eNSP 网络诊断工具

提供路由表、ARP 表、接口信息、运行配置查看,以及 Ping/Traceroute 等诊断功能。
支持结构化 JSON 输出。
"""
import logging
from typing import Optional
from netmiko import ConnectHandler
from ..config import get_device_info
from ..parsers import parse_interface_brief, parse_routing_table, parse_arp_table, to_json

logger = logging.getLogger("ensp_mcp.diagnostic")


def get_running_config(
    device_ip: str,
    section: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """获取设备当前运行配置"""
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            if section:
                command = f"display current-configuration | section {section}"
            else:
                command = "display current-configuration"
            output = net_connect.send_command(command)
            return f"[{device_ip}] 运行配置:\n{output}"
    except Exception as e:
        return f"获取运行配置失败 [{device_ip}]: {str(e)}"


def get_interface_info(
    device_ip: str,
    interface_name: Optional[str] = None,
    structured: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    获取设备接口信息

    Args:
        structured: 为 True 时返回 JSON 格式
    """
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            brief = net_connect.send_command("display ip interface brief")

            if structured:
                parsed = parse_interface_brief(brief)
                return to_json({"device": device_ip, "interfaces": parsed})

            result = f"[{device_ip}] 接口摘要:\n{brief}"

            if interface_name:
                detail = net_connect.send_command(
                    f"display interface {interface_name}"
                )
                result += f"\n\n[{device_ip}] {interface_name} 详细信息:\n{detail}"

            return result
    except Exception as e:
        return f"获取接口信息失败 [{device_ip}]: {str(e)}"


def get_routing_table(
    device_ip: str,
    protocol: Optional[str] = None,
    destination: Optional[str] = None,
    structured: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    获取设备路由表

    Args:
        structured: 为 True 时返回 JSON 格式
    """
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            if destination:
                command = f"display ip routing-table {destination}"
            elif protocol:
                command = f"display ip routing-table protocol {protocol}"
            else:
                command = "display ip routing-table"

            output = net_connect.send_command(command)

            if structured:
                parsed = parse_routing_table(output)
                return to_json({"device": device_ip, "routes": parsed})

            return f"[{device_ip}] 路由表:\n{output}"
    except Exception as e:
        return f"获取路由表失败 [{device_ip}]: {str(e)}"


def get_arp_table(
    device_ip: str,
    interface_name: Optional[str] = None,
    structured: bool = False,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    获取设备 ARP 表

    Args:
        structured: 为 True 时返回 JSON 格式
    """
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            if interface_name:
                command = f"display arp interface {interface_name}"
            else:
                command = "display arp all"

            output = net_connect.send_command(command)

            if structured:
                parsed = parse_arp_table(output)
                return to_json({"device": device_ip, "arp_entries": parsed})

            return f"[{device_ip}] ARP 表:\n{output}"
    except Exception as e:
        return f"获取 ARP 表失败 [{device_ip}]: {str(e)}"


def ping_from_device(
    device_ip: str,
    target_ip: str,
    count: int = 5,
    timeout: int = 2,
    source_ip: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """从设备发起 Ping 测试"""
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            command = f"ping -c {count} -t {timeout}"
            if source_ip:
                command += f" -a {source_ip}"
            command += f" {target_ip}"

            output = net_connect.send_command(command, read_timeout=count * timeout + 10)
            return f"[{device_ip}] Ping {target_ip} 结果:\n{output}"
    except Exception as e:
        return f"Ping 测试失败 [{device_ip}]: {str(e)}"


def traceroute_from_device(
    device_ip: str,
    target_ip: str,
    source_ip: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """从设备发起 Traceroute 测试"""
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            command = "tracert"
            if source_ip:
                command += f" -a {source_ip}"
            command += f" {target_ip}"

            output = net_connect.send_command(command, read_timeout=60)
            return f"[{device_ip}] Traceroute 到 {target_ip}:\n{output}"
    except Exception as e:
        return f"Traceroute 失败 [{device_ip}]: {str(e)}"


def get_interface_statistics(
    device_ip: str,
    interface_name: str,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    获取接口流量统计和错误计数器

    Args:
        device_ip: 设备名称或 IP
        interface_name: 接口名称（如 GigabitEthernet0/0/0）

    Returns:
        接口统计信息（包含收发字节/包数、错误计数等）
    """
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            output = net_connect.send_command(f"display interface {interface_name}")

            # 提取关键统计信息
            lines = [f"[{device_ip}] {interface_name} 流量统计:"]
            lines.append(output)

            return "\n".join(lines)
    except Exception as e:
        return f"获取接口统计失败 [{device_ip}]: {str(e)}"
