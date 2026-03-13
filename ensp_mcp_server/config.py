"""
eNSP MCP Server Configuration Module

提供设备连接配置和全局设置
支持通过 eNSP 本地 TCP 端口直连设备（无需配置 Cloud/Telnet）
"""
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from typing import Optional
from pydantic import BaseModel

from .exceptions import DeviceNotFoundError

# ==================== 日志配置 ====================

_log_level = os.environ.get("ENSP_LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.WARNING),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,  # 输出到 stderr，不干扰 stdio MCP 传输
)
logger = logging.getLogger("ensp_mcp")


# ==================== 配置模型 ====================

class DeviceCredentials(BaseModel):
    """设备连接凭证"""
    username: str = ""
    password: str = ""
    port: int = 23  # Telnet 默认端口
    device_type: str = "huawei_telnet"


class ServerConfig(BaseModel):
    """服务器配置"""
    server_name: str = "eNSP_NetOps_Server"
    timeout: int = 30
    global_delay_factor: float = 1.0


# 全局配置实例
config = ServerConfig()

# 凭证：支持 ENSP_USERNAME / ENSP_PASSWORD 环境变量
credentials = DeviceCredentials(
    username=os.environ.get("ENSP_USERNAME", ""),
    password=os.environ.get("ENSP_PASSWORD", ""),
)


# ==================== 设备注册表 ====================

# 设备注册表文件路径（与项目同目录）
_REGISTRY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "devices.json")

# 内存中的设备注册表
# 支持两种格式:
#   简单: { "R1": 2000 }
#   详细: { "R1": { "port": 2000, "username": "admin", "password": "xxx" } }
_device_registry: dict[str, int | dict] = {}


def _load_registry():
    """从文件加载设备注册表"""
    global _device_registry
    if os.path.exists(_REGISTRY_FILE):
        try:
            with open(_REGISTRY_FILE, "r", encoding="utf-8") as f:
                _device_registry = json.load(f)
            logger.info("设备注册表已加载: %d 台设备", len(_device_registry))
        except Exception as e:
            logger.error("加载注册表失败: %s", e)
            _device_registry = {}


def _save_registry():
    """保存设备注册表到文件"""
    with open(_REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(_device_registry, f, ensure_ascii=False, indent=2)
    logger.debug("注册表已保存")


def _get_device_port(entry: int | dict) -> int:
    """从注册表条目中提取端口号（兼容两种格式）"""
    if isinstance(entry, dict):
        return entry.get("port", 23)
    return entry


def _get_device_credentials(entry: int | dict) -> dict:
    """从注册表条目中提取 per-device 凭证覆盖"""
    if isinstance(entry, dict):
        return {
            k: v for k, v in entry.items()
            if k in ("username", "password", "device_type") and v
        }
    return {}


def register_device(name: str, port: int, username: str = "", password: str = "") -> str:
    """
    注册 eNSP 设备

    Args:
        name: 设备名称（如 R1, SW1）
        port: eNSP 分配的本地 TCP 端口号
        username: 可选，per-device 用户名覆盖
        password: 可选，per-device 密码覆盖

    Returns:
        注册结果信息
    """
    if username or password:
        entry: int | dict = {"port": port}
        if username:
            entry["username"] = username
        if password:
            entry["password"] = password
    else:
        entry = port

    _device_registry[name] = entry
    _save_registry()
    logger.info("设备 '%s' 已注册，端口: %d", name, port)
    return f"设备 '{name}' 已注册，端口: {port}"


def unregister_device(name: str) -> str:
    """注销设备"""
    if name in _device_registry:
        del _device_registry[name]
        _save_registry()
        logger.info("设备 '%s' 已注销", name)
        return f"设备 '{name}' 已注销"
    return f"设备 '{name}' 不存在"


def list_devices() -> str:
    """列出所有已注册设备"""
    if not _device_registry:
        return "当前没有注册任何设备。请使用 register_device 工具注册设备。"
    lines = ["已注册设备列表:"]
    lines.append(f"{'设备名':<12} {'端口':<8}")
    lines.append("-" * 20)
    for name, entry in _device_registry.items():
        port = _get_device_port(entry)
        lines.append(f"{name:<12} {port:<8}")
    return "\n".join(lines)


def _resolve_device(device_ip: str) -> tuple[str, int]:
    """
    解析设备标识，返回 (host, port)

    支持以下格式:
    - 设备名: "R1" → ("127.0.0.1", 注册表中的端口)
    - IP地址: "192.168.56.10" → ("192.168.56.10", 23) (传统模式)
    """
    # 先从注册表查找
    if device_ip in _device_registry:
        return ("127.0.0.1", _get_device_port(_device_registry[device_ip]))

    # 不区分大小写再查一次
    for name, entry in _device_registry.items():
        if name.lower() == device_ip.lower():
            return ("127.0.0.1", _get_device_port(entry))

    # 回退到传统 IP 模式
    logger.debug("设备 '%s' 未在注册表中找到，回退到 IP 模式", device_ip)
    return (device_ip, credentials.port)


# 设备连接模板
def get_device_info(
    ip: str,
    username: Optional[str] = None,
    password: Optional[str] = None,
    port: Optional[int] = None,
    device_type: Optional[str] = None
) -> dict:
    """
    获取设备连接信息

    Args:
        ip: 设备名称（如 R1）或 IP 地址
        username: 用户名（可选）
        password: 密码（可选）
        port: 端口（可选，覆盖自动解析）
        device_type: 设备类型（可选）

    Returns:
        设备连接字典（供 netmiko ConnectHandler 使用）
    """
    host, resolved_port = _resolve_device(ip)

    # 查找 per-device 凭证覆盖
    per_device = {}
    if ip in _device_registry:
        per_device = _get_device_credentials(_device_registry[ip])
    else:
        for name, entry in _device_registry.items():
            if name.lower() == ip.lower():
                per_device = _get_device_credentials(entry)
                break

    # 优先级: 函数参数 > per-device > 环境变量/全局默认
    info = {
        'device_type': device_type or per_device.get('device_type') or credentials.device_type,
        'host': host,
        'username': username or per_device.get('username') or credentials.username,
        'password': password or per_device.get('password') or credentials.password,
        'port': port or resolved_port,
        'global_delay_factor': config.global_delay_factor,
        'timeout': config.timeout,
    }
    logger.debug("设备连接信息: %s:%d type=%s", info['host'], info['port'], info['device_type'])
    return info


def auto_discover_devices() -> str:
    """
    自动发现 eNSP 设备：
    1. 查找 eNSP_Client.exe 进程的 PID
    2. 获取该进程监听的所有 TCP 端口（2000-2999 范围）
    3. 连接每个端口，通过 sysname 获取设备名称
    4. 合并更新 devices.json（保留手动注册的设备）
    """
    # 1. 查找 eNSP 进程 PID
    try:
        result = subprocess.run(
            ['tasklist'], capture_output=True, text=True, timeout=10
        )
    except Exception as e:
        return f"无法获取进程列表: {e}"

    pids = set()
    for line in result.stdout.split('\n'):
        if 'eNSP_Client' in line:
            parts = line.split()
            if len(parts) >= 2:
                pids.add(parts[1])

    if not pids:
        return "未检测到 eNSP_Client.exe 进程，请确认 eNSP 已启动且设备已运行。"

    # 2. 获取这些 PID 监听的控制台端口
    try:
        result = subprocess.run(
            ['netstat', '-ano'], capture_output=True, text=True, timeout=10
        )
    except Exception as e:
        return f"无法获取端口信息: {e}"

    ports = []
    for line in result.stdout.split('\n'):
        if 'LISTENING' not in line or '0.0.0.0:' not in line:
            continue
        for pid in pids:
            if line.strip().endswith(pid):
                match = re.search(r'0\.0\.0\.0:(\d+)', line)
                if match:
                    port = int(match.group(1))
                    if 2000 <= port <= 2999:
                        ports.append(port)

    if not ports:
        return (
            f"找到 eNSP 进程 (PID: {', '.join(pids)})，"
            "但未检测到控制台端口（2000-2999 范围）。请确认设备已启动。"
        )

    ports.sort()

    # 3. 连接每个端口获取 sysname
    discovered = {}
    default_count = 0
    for port in ports:
        sysname = _get_sysname_from_port(port)
        if sysname and sysname != "Huawei":
            discovered[sysname] = port
        else:
            # 默认主机名，按端口顺序自动命名
            default_count += 1
            auto_name = f"Device{default_count}"
            discovered[auto_name] = port

    # 4. 合并更新注册表（保留手动注册的设备）
    # 收集已发现端口集合
    discovered_ports = set(discovered.values())
    # 保留手动注册的设备（端口不在本次发现范围内的）
    merged = {}
    for name, entry in _device_registry.items():
        existing_port = _get_device_port(entry)
        if existing_port not in discovered_ports:
            merged[name] = entry
    # 添加/更新已发现的设备
    for name, port in discovered.items():
        merged[name] = port

    global _device_registry
    _device_registry = merged
    _save_registry()

    logger.info("自动发现完成: 发现 %d 台设备，注册表共 %d 台", len(discovered), len(_device_registry))

    # 生成报告
    lines = [f"自动发现完成！共找到 {len(discovered)} 台设备:"]
    lines.append(f"{'设备名':<12} {'端口':<8}")
    lines.append("-" * 20)
    for name, port in discovered.items():
        lines.append(f"{name:<12} {port:<8}")

    # 显示保留的手动注册设备
    manual_kept = {k: v for k, v in merged.items() if k not in discovered}
    if manual_kept:
        lines.append(f"\n已保留 {len(manual_kept)} 台手动注册设备:")
        for name, entry in manual_kept.items():
            lines.append(f"  {name:<12} {_get_device_port(entry):<8}")

    if default_count > 0:
        lines.append(
            f"\n提示: {default_count} 台设备使用默认主机名(Huawei)，"
            "已自动命名。建议在 eNSP 中用 'sysname R1' 设置主机名后重新发现。"
        )
    return "\n".join(lines)


def _get_sysname_from_port(port: int) -> str | None:
    """通过控制台端口连接设备，获取 sysname"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(('127.0.0.1', port))
        time.sleep(0.5)
        s.sendall(b'\r\n')
        time.sleep(1)
        s.recv(4096)  # 清空缓冲区
        s.sendall(b'display current-configuration | include sysname\r\n')
        time.sleep(2)
        data = s.recv(4096).decode('utf-8', errors='ignore')
        s.close()
        for line in data.split('\n'):
            stripped = line.strip()
            if stripped.startswith('sysname ') and 'display' not in stripped:
                return stripped.replace('sysname ', '')
        return None
    except Exception:
        return None


# 启动时加载注册表
_load_registry()
