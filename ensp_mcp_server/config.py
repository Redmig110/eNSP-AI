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
#
# ⚠ 只能**原地修改**这个 dict，绝不能重新绑定（`_device_registry = ...`）：
#   cli.py / inspection.py 用的是 `from ..config import _device_registry`，
#   导入时就把名字绑死在这个对象上。一旦重新绑定，它们手里还是旧字典 ——
#   表现是 auto_discover 之后 inspect_topology / multi_health_check 仍在巡检
#   discover 之前那批设备，_looks_registered 也会把刚发现的设备误报成未注册。
_device_registry: dict[str, int | dict] = {}


def _replace_registry(data: dict):
    """原地替换注册表内容 —— 保持对象身份不变，见上面的注释。"""
    _device_registry.clear()
    _device_registry.update(data)


def _load_registry():
    if os.path.exists(_REGISTRY_FILE):
        try:
            with open(_REGISTRY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            _replace_registry(data)
            logger.info("设备注册表已加载: %d 台设备", len(_device_registry))
        except Exception as e:
            logger.error("加载注册表失败: %s", e)
            _replace_registry({})


def _save_registry():
    with open(_REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(_device_registry, f, ensure_ascii=False, indent=2)


def _get_device_port(entry: int | dict) -> int:
    if isinstance(entry, dict):
        return entry.get("port", 23)
    return entry


def _get_device_credentials(entry: int | dict) -> tuple[str, str]:
    """取设备用户名/密码。交换机免登录，返回空串即可；USG6000V 必须给。"""
    if isinstance(entry, dict):
        return entry.get("username", ""), entry.get("password", "")
    return "", ""


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

    # 现有条目按端口 / 名字建索引，用来保留手动注册的凭据和名字。
    # 关键：USG6000V 这类设备读不到 sysname（控制台先要登录），必须靠端口
    # 认回来；否则它会被重命名成 DeviceN，并且被裸端口覆盖掉 username/password。
    existing_by_port = {}
    existing_by_name = {}
    for name, entry in _device_registry.items():
        existing_by_port.setdefault(_get_device_port(entry), (name, entry))
        existing_by_name[name] = entry

    # 3. 连接获取 sysname
    discovered = {}
    default_count = 0
    for port in ports:
        sysname = _get_sysname_from_port(port)
        existing = existing_by_port.get(port)

        if sysname and sysname != "Huawei":
            name = sysname
            # 端口对得上优先用端口的旧条目；其次按 sysname 找同名旧条目。
            old = existing[1] if existing else existing_by_name.get(name)
        elif existing:
            # 读不到 sysname（需要登录的设备）：保留原名字和凭据。
            name = existing[0]
            old = existing[1]
        else:
            default_count += 1
            name = f"Device{default_count}"
            old = None

        # 两台设备同名时加后缀，避免后发现的覆盖先前发现的。
        unique = name
        seq = 2
        while unique in discovered:
            unique = f"{name}-{seq}"
            seq += 1

        if isinstance(old, dict):
            entry = dict(old)
            entry["port"] = port
            discovered[unique] = entry
        else:
            discovered[unique] = port

    # 4. 合并注册表（保留没被本次发现覆盖的手动注册设备）
    discovered_ports = {_get_device_port(e) for e in discovered.values()}
    merged = {}
    for name, entry in _device_registry.items():
        if _get_device_port(entry) not in discovered_ports:
            merged[name] = entry
    merged.update(discovered)

    # 原地更新，不重新绑定 —— 见 _device_registry 定义处的注释
    _replace_registry(merged)
    _save_registry()

    # 生成报告
    lines = [f"自动发现完成！共找到 {len(discovered)} 台设备:"]
    lines.append(f"{'设备名':<12} {'端口':<8}")
    lines.append("-" * 20)
    for name, entry in discovered.items():
        lines.append(f"{name:<12} {_get_device_port(entry):<8}")

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
