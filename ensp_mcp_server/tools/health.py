"""
eNSP 设备健康检查工具

提供 TCP 端口检测 + 轻量命令测试的健康检查功能。
"""
import logging
import socket
from typing import Optional
from netmiko import ConnectHandler
from ..config import get_device_info, _resolve_device, _device_registry, _get_device_port

logger = logging.getLogger("ensp_mcp.health")


def health_check(
    device_ip: str,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    单台设备健康检查

    1. TCP 端口连通性检测
    2. 轻量命令测试（display clock）

    Args:
        device_ip: 设备名称或 IP

    Returns:
        健康检查报告
    """
    host, port = _resolve_device(device_ip)
    results = [f"[{device_ip}] 健康检查报告:"]

    # 1. TCP 端口检测
    tcp_ok = False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((host, port))
        s.close()
        tcp_ok = True
        results.append(f"  TCP 连接 ({host}:{port}): ✓ 正常")
    except Exception as e:
        results.append(f"  TCP 连接 ({host}:{port}): ✗ 失败 - {e}")
        return "\n".join(results)

    # 2. 命令测试
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            output = net_connect.send_command("display clock", read_timeout=10)
            results.append(f"  CLI 命令测试: ✓ 正常")
            results.append(f"  设备时间: {output.strip()}")
    except Exception as e:
        results.append(f"  CLI 命令测试: ✗ 失败 - {e}")

    return "\n".join(results)


def multi_health_check(
    device_names: list[str] | None = None,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    批量健康检查

    Args:
        device_names: 设备名称列表，为空则检查所有注册设备

    Returns:
        批量健康检查报告
    """
    if not device_names:
        if not _device_registry:
            return "没有注册任何设备，请先注册设备。"
        device_names = list(_device_registry.keys())

    results = [f"批量健康检查（{len(device_names)} 台设备）:"]
    results.append("=" * 50)

    healthy = 0
    for name in device_names:
        report = health_check(name, username, password)
        if "✓ 正常" in report and "✗" not in report:
            healthy += 1
        results.append(report)
        results.append("")

    results.append(f"汇总: {healthy}/{len(device_names)} 台设备健康")
    return "\n".join(results)
