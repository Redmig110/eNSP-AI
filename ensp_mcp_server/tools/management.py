"""
eNSP 设备管理工具

提供设备版本查看、配置备份、配置对比、设备重启、配置回滚等管理功能。
"""
import difflib
import json
import logging
import os
import time
from typing import Optional
from netmiko import ConnectHandler
from ..config import get_device_info

logger = logging.getLogger("ensp_mcp.management")

# 配置快照存储目录
_SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config_snapshots")


def get_device_version(
    device_ip: str,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """获取设备版本和硬件信息"""
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            results = []

            output = net_connect.send_command("display version")
            results.append(f"设备版本:\n{output}")

            output = net_connect.send_command("display device")
            results.append(f"\n设备硬件:\n{output}")

            return f"[{device_ip}] 设备信息:\n" + "\n".join(results)
    except Exception as e:
        return f"获取设备信息失败 [{device_ip}]: {str(e)}"


def backup_config(
    device_ip: str,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """备份设备当前运行配置(返回完整配置文本)"""
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            output = net_connect.send_command("display current-configuration")
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            return (
                f"# ========================================\n"
                f"# 设备配置备份 [{device_ip}]\n"
                f"# 备份时间: {timestamp}\n"
                f"# ========================================\n\n"
                f"{output}"
            )
    except Exception as e:
        return f"配置备份失败 [{device_ip}]: {str(e)}"


def compare_config(
    device_ip: str,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    对比设备当前运行配置与已保存配置的差异

    使用 difflib.unified_diff 对比 display current-configuration 与
    display saved-configuration 的输出。
    """
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            running = net_connect.send_command("display current-configuration")
            saved = net_connect.send_command("display saved-configuration")

            running_lines = running.splitlines(keepends=True)
            saved_lines = saved.splitlines(keepends=True)

            diff = list(difflib.unified_diff(
                saved_lines,
                running_lines,
                fromfile="saved-configuration",
                tofile="current-configuration",
                lineterm="",
            ))

            if not diff:
                return f"[{device_ip}] 当前配置与保存配置一致，无差异。"

            return f"[{device_ip}] 配置差异:\n" + "\n".join(diff)
    except Exception as e:
        return f"配置对比失败 [{device_ip}]: {str(e)}"


def reboot_device(
    device_ip: str,
    save_before_reboot: bool = True,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    重启网络设备(危险操作,会导致设备短暂中断)

    发送 Y 确认后捕获断连异常，这是预期行为。
    """
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            if save_before_reboot:
                # 使用 send_command_timing 处理 save 的 [Y/N] 交互
                save_output = net_connect.send_command_timing("save")
                if "[Y/N]" in save_output or "[y/n]" in save_output:
                    save_output += net_connect.send_command_timing("Y")
                logger.info("[%s] 重启前配置已保存", device_ip)

            output = net_connect.send_command_timing("reboot")
            # 设备可能需要确认是否保存（如果未保存），以及确认重启
            if "[Y/N]" in output or "[y/n]" in output:
                try:
                    output += net_connect.send_command_timing("Y")
                except Exception:
                    pass  # 发送 Y 后设备断连是预期行为

            # 可能还有第二次确认
            if "[Y/N]" in output or "[y/n]" in output:
                try:
                    output += net_connect.send_command_timing("Y")
                except Exception:
                    pass

        return f"[{device_ip}] 设备正在重启...\n{output}"
    except (OSError, EOFError):
        # 重启后连接断开是预期行为
        logger.info("[%s] 设备重启中，连接已断开（预期行为）", device_ip)
        return f"[{device_ip}] 设备正在重启...连接已断开（预期行为）。"
    except Exception as e:
        # 如果是连接关闭相关的异常，也视为成功
        err_str = str(e).lower()
        if any(kw in err_str for kw in ("socket is closed", "not open", "eof", "connection reset")):
            logger.info("[%s] 设备重启中，连接已断开（预期行为）", device_ip)
            return f"[{device_ip}] 设备正在重启...连接已断开（预期行为）。"
        return f"重启设备失败 [{device_ip}]: {str(e)}"


def get_log_info(
    device_ip: str,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """获取设备日志信息"""
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            output = net_connect.send_command("display logbuffer")
            return f"[{device_ip}] 设备日志:\n{output}"
    except Exception as e:
        return f"获取日志失败 [{device_ip}]: {str(e)}"


def get_cpu_memory(
    device_ip: str,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """获取设备 CPU 和内存使用情况"""
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            results = []

            output = net_connect.send_command("display cpu-usage")
            results.append(f"CPU 使用率:\n{output}")

            output = net_connect.send_command("display memory-usage")
            results.append(f"\n内存使用率:\n{output}")

            return f"[{device_ip}] 资源使用:\n" + "\n".join(results)
    except Exception as e:
        return f"获取资源使用失败 [{device_ip}]: {str(e)}"


# ==================== 配置回滚 ====================

def save_config_snapshot(
    device_ip: str,
    label: str = "",
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    保存设备配置快照到本地

    Args:
        device_ip: 设备名称或 IP
        label: 可选标签，便于标识
    """
    try:
        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            config_text = net_connect.send_command("display current-configuration")

        os.makedirs(_SNAPSHOT_DIR, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        safe_name = device_ip.replace(".", "_").replace(":", "_")
        label_part = f"_{label}" if label else ""
        filename = f"{safe_name}{label_part}_{timestamp}.txt"
        filepath = os.path.join(_SNAPSHOT_DIR, filename)

        # 保存快照元数据和配置
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# device: {device_ip}\n")
            f.write(f"# label: {label}\n")
            f.write(f"# timestamp: {timestamp}\n")
            f.write(f"# ---\n")
            f.write(config_text)

        logger.info("[%s] 配置快照已保存: %s", device_ip, filename)
        return f"[{device_ip}] 配置快照已保存: {filename}"
    except Exception as e:
        return f"保存快照失败 [{device_ip}]: {str(e)}"


def list_config_snapshots(device_ip: str = "") -> str:
    """
    列出配置快照

    Args:
        device_ip: 可选，过滤特定设备的快照
    """
    if not os.path.exists(_SNAPSHOT_DIR):
        return "没有配置快照。"

    files = sorted(os.listdir(_SNAPSHOT_DIR), reverse=True)
    if device_ip:
        safe_name = device_ip.replace(".", "_").replace(":", "_")
        files = [f for f in files if f.startswith(safe_name)]

    if not files:
        filter_msg = f"（设备: {device_ip}）" if device_ip else ""
        return f"没有配置快照{filter_msg}。"

    lines = [f"配置快照列表（共 {len(files)} 个）:"]
    for f in files[:20]:  # 最多显示 20 个
        lines.append(f"  {f}")
    if len(files) > 20:
        lines.append(f"  ... 还有 {len(files) - 20} 个")
    return "\n".join(lines)


def rollback_config(
    device_ip: str,
    snapshot_filename: str,
    username: Optional[str] = None,
    password: Optional[str] = None
) -> str:
    """
    从快照回滚配置

    Args:
        device_ip: 设备名称或 IP
        snapshot_filename: 快照文件名
    """
    filepath = os.path.join(_SNAPSHOT_DIR, snapshot_filename)
    if not os.path.exists(filepath):
        return f"快照文件不存在: {snapshot_filename}"

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 跳过元数据头部
        config_lines = []
        past_header = False
        for line in lines:
            if past_header:
                config_lines.append(line.rstrip())
            elif line.startswith("# ---"):
                past_header = True

        if not config_lines:
            return f"快照文件为空或格式错误: {snapshot_filename}"

        # 过滤掉非配置行（如 sysname 等全局命令需要保留）
        # 移除 return/quit 等行和空行开头的注释
        commands = [l for l in config_lines if l.strip() and not l.strip().startswith('#')]

        device = get_device_info(device_ip, username, password)
        with ConnectHandler(**device) as net_connect:
            output = net_connect.send_config_set(commands)

        logger.info("[%s] 配置已从快照回滚: %s", device_ip, snapshot_filename)
        return f"[{device_ip}] 配置回滚完成（来源: {snapshot_filename}）:\n{output}"
    except Exception as e:
        return f"配置回滚失败 [{device_ip}]: {str(e)}"
