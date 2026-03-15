"""
eNSP MCP Server Configuration Module

设备注册表管理 + 自动发现。
通过 eNSP 本地 TCP 端口直连设备（无需配置 Cloud/Telnet）。
"""
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from pydantic import BaseModel

from .exceptions import DeviceNotFoundError

# ==================== 日志配置 ====================

_log_level = os.environ.get("ENSP_LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.WARNING),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("ensp_mcp")


# ==================== 配置模型 ====================

class ServerConfig(BaseModel):
    """服务器配置"""
    server_name: str = "eNSP_NetOps_Server"
    timeout: int = 30


# 全局配置实例
config = ServerConfig()


# ==================== 设备注册表 ====================

_REGISTRY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "devices.json")

# 支持两种格式:
#   简单: { "R1": 2000 }
#   详细: { "R1": { "port": 2000, "username": "admin", "password": "xxx" } }
_device_registry: dict[str, int | dict] = {}


def _load_registry():
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
    with open(_REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(_device_registry, f, ensure_ascii=False, indent=2)


def _get_device_port(entry: int | dict) -> int:
    if isinstance(entry, dict):
        return entry.get("port", 23)
    return entry


def register_device(name: str, port: int, username: str = "", password: str = "") -> str:
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
    return f"设备 '{name}' 已注册，端口: {port}"


def unregister_device(name: str) -> str:
    if name in _device_registry:
        del _device_registry[name]
        _save_registry()
        return f"设备 '{name}' 已注销"
    return f"设备 '{name}' 不存在"


def list_devices() -> str:
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

    - 设备名: "R1" → ("127.0.0.1", 注册表中的端口)
    - IP地址: "192.168.56.10" → ("192.168.56.10", 23)
    """
    if device_ip in _device_registry:
        return ("127.0.0.1", _get_device_port(_device_registry[device_ip]))

    for name, entry in _device_registry.items():
        if name.lower() == device_ip.lower():
            return ("127.0.0.1", _get_device_port(entry))

    logger.debug("设备 '%s' 未在注册表中找到，回退到 IP 模式", device_ip)
    return (device_ip, 23)


# ==================== 自动发现 ====================

def auto_discover_devices() -> str:
    """
    自动发现 eNSP 设备：
    1. 查找 eNSP_Client.exe 进程 PID
    2. 获取监听的 TCP 端口（2000-2999）
    3. 连接获取 sysname
    4. 合并更新注册表（保留手动注册的设备）
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

    # 2. 获取控制台端口
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

    # 3. 连接获取 sysname
    discovered = {}
    default_count = 0
    for port in ports:
        sysname = _get_sysname_from_port(port)
        if sysname and sysname != "Huawei":
            discovered[sysname] = port
        else:
            default_count += 1
            discovered[f"Device{default_count}"] = port

    # 4. 合并注册表（保留手动注册的设备）
    global _device_registry
    discovered_ports = set(discovered.values())
    merged = {}
    for name, entry in _device_registry.items():
        if _get_device_port(entry) not in discovered_ports:
            merged[name] = entry
    for name, port in discovered.items():
        merged[name] = port

    _device_registry = merged
    _save_registry()

    # 生成报告
    lines = [f"自动发现完成！共找到 {len(discovered)} 台设备:"]
    lines.append(f"{'设备名':<12} {'端口':<8}")
    lines.append("-" * 20)
    for name, port in discovered.items():
        lines.append(f"{name:<12} {port:<8}")

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
        s.recv(4096)
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
