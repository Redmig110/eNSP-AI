"""
eNSP CLI 工具集

提供 CLI 命令执行、配置下发、保存、运行配置查看、
Ping/Traceroute 诊断、健康检查等核心功能。
"""
import logging
import socket
from typing import Optional

from ..config import _resolve_device, _device_registry, _get_device_port
from ..console import HuaweiConsole

logger = logging.getLogger("ensp_mcp.cli")


def _connect(device_ip: str, timeout: int = 15) -> HuaweiConsole:
    """根据设备名/IP 创建 HuaweiConsole 实例"""
    host, port = _resolve_device(device_ip)
    return HuaweiConsole(host, port, timeout=timeout)


# ==================== 核心工具 ====================

def execute_cli(device_ip: str, command: str, timeout: int = 30) -> str:
    """在设备上执行单条查询/显示命令"""
    try:
        with _connect(device_ip, timeout=timeout) as c:
            output = c.send_command(command, timeout=timeout)
            return f"[{device_ip}] 执行 '{command}' 结果:\n{output}"
    except Exception as e:
        return f"无法连接设备 {device_ip} 或执行失败: {e}"


def push_config(device_ip: str, commands: list[str]) -> str:
    """向设备下发一组配置命令"""
    try:
        with _connect(device_ip) as c:
            output = c.send_config_set(commands)
            return f"[{device_ip}] 配置下发结果:\n{output}"
    except Exception as e:
        return f"配置下发失败 [{device_ip}]: {e}"


def multi_device_push_config(devices: list[dict]) -> str:
    """同时向多台设备下发配置"""
    results = []
    for entry in devices:
        device_ip = entry.get("device_ip", "")
        commands = entry.get("commands", [])
        if device_ip and commands:
            results.append(push_config(device_ip, commands))
    return "\n\n".join(results)


def save_config(device_ip: str) -> str:
    """保存设备配置（处理 [Y/N] 交互确认）"""
    try:
        with _connect(device_ip) as c:
            output = c.send_command_timing("save", delay=2.0)
            if "[Y/N]" in output or "[y/n]" in output:
                c._send_line("Y")
                import time
                time.sleep(3)
                output += c._read_available()
            return f"[{device_ip}] 配置保存结果:\n{output}"
    except Exception as e:
        return f"保存配置失败 [{device_ip}]: {e}"


def get_running_config(device_ip: str, section: Optional[str] = None) -> str:
    """获取设备当前运行配置，可选 section 过滤"""
    try:
        with _connect(device_ip, timeout=30) as c:
            if section:
                command = f"display current-configuration | section {section}"
            else:
                command = "display current-configuration"
            output = c.send_command(command, timeout=30)
            return f"[{device_ip}] 运行配置:\n{output}"
    except Exception as e:
        return f"获取运行配置失败 [{device_ip}]: {e}"


# ==================== 诊断工具 ====================

def ping_from_device(
    device_ip: str,
    target_ip: str,
    count: int = 5,
    source_ip: Optional[str] = None,
) -> str:
    """从设备发起 Ping 测试"""
    try:
        command = f"ping -c {count} -t 2"
        if source_ip:
            command += f" -a {source_ip}"
        command += f" {target_ip}"

        timeout = count * 2 + 10
        with _connect(device_ip, timeout=timeout) as c:
            output = c.send_command(command, timeout=timeout)
            return f"[{device_ip}] Ping {target_ip} 结果:\n{output}"
    except Exception as e:
        return f"Ping 测试失败 [{device_ip}]: {e}"


def traceroute_from_device(
    device_ip: str,
    target_ip: str,
    source_ip: Optional[str] = None,
) -> str:
    """从设备发起 Traceroute"""
    try:
        command = "tracert"
        if source_ip:
            command += f" -a {source_ip}"
        command += f" {target_ip}"

        with _connect(device_ip, timeout=60) as c:
            output = c.send_command(command, timeout=60)
            return f"[{device_ip}] Traceroute 到 {target_ip}:\n{output}"
    except Exception as e:
        return f"Traceroute 失败 [{device_ip}]: {e}"


# ==================== 健康检查 ====================

def health_check(device_ip: str) -> str:
    """单台设备健康检查：TCP 端口检测 + 轻量命令测试"""
    host, port = _resolve_device(device_ip)
    results = [f"[{device_ip}] 健康检查报告:"]

    # 1. TCP 端口检测
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((host, port))
        s.close()
        results.append(f"  TCP 连接 ({host}:{port}): OK")
    except Exception as e:
        results.append(f"  TCP 连接 ({host}:{port}): FAIL - {e}")
        return "\n".join(results)

    # 2. 命令测试
    try:
        with HuaweiConsole(host, port, timeout=10) as c:
            output = c.send_command("display clock", timeout=10)
            results.append(f"  CLI 命令测试: OK")
            results.append(f"  设备时间: {output.strip()}")
    except Exception as e:
        results.append(f"  CLI 命令测试: FAIL - {e}")

    return "\n".join(results)


def multi_health_check(device_names: list[str] | None = None) -> str:
    """批量健康检查"""
    if not device_names:
        if not _device_registry:
            return "没有注册任何设备，请先注册设备。"
        device_names = list(_device_registry.keys())

    results = [f"批量健康检查（{len(device_names)} 台设备）:"]
    results.append("=" * 50)

    healthy = 0
    for name in device_names:
        report = health_check(name)
        if "FAIL" not in report:
            healthy += 1
        results.append(report)
        results.append("")

    results.append(f"汇总: {healthy}/{len(device_names)} 台设备健康")
    return "\n".join(results)
