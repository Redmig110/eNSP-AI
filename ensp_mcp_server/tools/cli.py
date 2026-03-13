"""
eNSP CLI Execution Tools

提供 CLI 命令执行功能
"""
import logging
from typing import Optional
from netmiko import ConnectHandler
from ..config import get_device_info
from ..exceptions import ConnectionError, CommandError, CommandTimeoutError

logger = logging.getLogger("ensp_mcp.cli")


def execute_cli(
    device_ip: str,
    command: str,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    在指定的 eNSP 网络设备上执行单条查询/显示命令

    Args:
        device_ip: 设备 IP 地址
        command: 要执行的命令（如 display ip interface brief）
        username: 用户名（可选，使用默认配置）
        password: 密码（可选，使用默认配置）

    Returns:
        命令执行结果
    """
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            output = net_connect.send_command(command)
            return f"[{device_ip}] 执行 '{command}' 结果:\n{output}"
    except ConnectionError:
        raise
    except Exception as e:
        return f"无法连接设备 {device_ip} 或执行失败: {str(e)}"


def push_config(
    device_ip: str,
    commands: list[str],
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    向 eNSP 网络设备下发配置

    Args:
        device_ip: 设备 IP 地址
        commands: 配置命令列表
        username: 用户名（可选）
        password: 密码（可选）

    Returns:
        配置结果
    """
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            output = net_connect.send_config_set(commands)
            return f"[{device_ip}] 配置下发结果:\n{output}"
    except Exception as e:
        return f"配置下发失败: {str(e)}"


def save_config(
    device_ip: str,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    保存设备配置（处理 [Y/N] 交互确认）

    Args:
        device_ip: 设备 IP 地址
        username: 用户名（可选）
        password: 密码（可选）

    Returns:
        保存结果
    """
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            # 使用 send_command_timing 处理 save 的 [Y/N] 交互
            output = net_connect.send_command_timing("save")
            if "[Y/N]" in output or "[y/n]" in output:
                output += net_connect.send_command_timing("Y")
            logger.info("[%s] 配置已保存", device_ip)
            return f"[{device_ip}] 配置保存结果:\n{output}"
    except Exception as e:
        return f"保存配置失败 [{device_ip}]: {str(e)}"


def batch_config(
    device_ip: str,
    config_list: list[dict],
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    批量配置多个设备

    Args:
        device_ip: 设备 IP 地址
        config_list: 配置列表，每个元素为 {"commands": [...]}
        username: 用户名（可选）
        password: 密码（可选）

    Returns:
        批量配置结果
    """
    results = []
    for i, config in enumerate(config_list):
        commands = config.get("commands", [])
        if commands:
            result = push_config(device_ip, commands, username, password)
            results.append(f"配置批次 {i+1}: {result}")

    return "\n".join(results)


def multi_device_push_config(
    devices: list[dict],
) -> str:
    """
    同时向多台设备下发配置

    Args:
        devices: 设备列表，每个元素为 {"device_ip": "R1", "commands": ["cmd1", ...]}

    Returns:
        所有设备的配置结果
    """
    results = []
    for entry in devices:
        device_ip = entry.get("device_ip", "")
        commands = entry.get("commands", [])
        if device_ip and commands:
            result = push_config(device_ip, commands)
            results.append(result)
    return "\n\n".join(results)
