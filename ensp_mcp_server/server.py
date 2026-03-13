"""
eNSP MCP Server 入口

使用底层 mcp.server.Server API 注册所有工具并启动服务。
v0.3.0: 分发字典替换 if/elif 链，asyncio.to_thread 包装，新增拓扑/健康/回滚等工具。
"""
import asyncio
import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .config import config, register_device, unregister_device, list_devices, auto_discover_devices

from .tools.cli import execute_cli, push_config, save_config, batch_config, multi_device_push_config
from .tools.diagnostic import (
    get_running_config, get_interface_info, get_routing_table,
    get_arp_table, ping_from_device, traceroute_from_device,
    get_interface_statistics,
)
from .tools.protocol import (
    check_ospf_neighbors, check_bgp_neighbors, check_acl,
    check_nat, check_stp, check_vlan
)
from .tools.management import (
    get_device_version, backup_config, compare_config,
    reboot_device, get_log_info, get_cpu_memory,
    save_config_snapshot, list_config_snapshots, rollback_config,
)
from .tools.topology import discover_topology, find_topo_files
from .tools.health import health_check, multi_health_check

logger = logging.getLogger("ensp_mcp.server")

# 初始化底层 MCP Server
server = Server(config.server_name)

# ==================== 工具定义 ====================

TOOLS = [
    # --- 设备注册工具 ---
    Tool(
        name="register_device",
        description=(
            "注册 eNSP 设备。提供设备名称和 eNSP 分配的本地 TCP 端口号。\n"
            "注册后即可用设备名（如 R1、SW1）代替 IP 地址来操作设备。\n"
            "端口号可在 eNSP 中右键设备 → 设置 → 串口号 查看。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "设备名称，如 R1, R2, SW1"},
                "port": {"type": "integer", "description": "eNSP 本地 TCP 端口号，如 2000, 2001"},
                "username": {"type": "string", "description": "设备用户名（可选，per-device 覆盖）"},
                "password": {"type": "string", "description": "设备密码（可选，per-device 覆盖）"},
            },
            "required": ["name", "port"],
        },
    ),
    Tool(
        name="unregister_device",
        description="注销已注册的 eNSP 设备。",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要注销的设备名称"},
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="list_devices",
        description="列出所有已注册的 eNSP 设备及其端口号。",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="auto_discover",
        description=(
            "自动发现 eNSP 设备。扫描 eNSP 进程监听的控制台端口，"
            "连接每台设备获取主机名，合并更新设备注册表（保留手动注册的设备）。"
        ),
        inputSchema={"type": "object", "properties": {}},
    ),

    # --- CLI 基础工具 ---
    Tool(
        name="execute_cli",
        description=(
            "在指定的 eNSP 网络设备上执行单条查询/显示命令,用于排错。\n"
            "例如:display ip interface brief, display ospf peer 等。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "command": {"type": "string", "description": "要执行的命令"},
            },
            "required": ["device_ip", "command"],
        },
    ),
    Tool(
        name="push_config",
        description=(
            "向 eNSP 网络设备下发配置。\n"
            "传入的是一个配置命令的列表,例如:['vlan 10', 'interface g0/0/1', 'port link-type access']"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "配置命令列表",
                },
            },
            "required": ["device_ip", "commands"],
        },
    ),
    Tool(
        name="save_config",
        description="保存 eNSP 网络设备的当前配置(执行 save,自动处理 [Y/N] 交互确认)。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="batch_config",
        description=(
            "批量向 eNSP 设备下发多组配置。\n"
            '传入一个列表,每个元素为 {"commands": ["cmd1", "cmd2", ...]},将依次执行每组配置。'
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "config_list": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "commands": {
                                "type": "array",
                                "items": {"type": "string"},
                            }
                        },
                    },
                    "description": "配置批次列表",
                },
            },
            "required": ["device_ip", "config_list"],
        },
    ),
    Tool(
        name="multi_device_push_config",
        description=(
            "同时向多台 eNSP 设备下发配置。\n"
            '传入设备列表,每个元素为 {"device_ip": "R1", "commands": ["cmd1", ...]}'
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "devices": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "device_ip": {"type": "string"},
                            "commands": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["device_ip", "commands"],
                    },
                    "description": "设备及命令列表",
                },
            },
            "required": ["devices"],
        },
    ),

    # --- 网络诊断工具 ---
    Tool(
        name="get_running_config",
        description=(
            "获取 eNSP 设备的当前运行配置。\n"
            '可选指定 section 过滤配置段落(如 "interface", "ospf", "bgp"),不传则返回全部配置。'
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "section": {"type": "string", "description": "过滤配置段落(可选)"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="get_interface_info",
        description=(
            "获取 eNSP 设备接口信息。\n"
            '不指定 interface_name 时返回所有接口摘要;指定时返回详细信息。\n'
            'structured=true 时返回 JSON 格式。'
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "interface_name": {"type": "string", "description": "接口名称(可选)"},
                "structured": {"type": "boolean", "default": False, "description": "返回 JSON 结构化数据"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="get_routing_table",
        description=(
            "获取 eNSP 设备路由表。\n"
            "可选按协议过滤(ospf/bgp/static/direct),或查询特定目的网络路由。\n"
            "structured=true 时返回 JSON 格式。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "protocol": {"type": "string", "description": "路由协议过滤(可选)"},
                "destination": {"type": "string", "description": "目的网络(可选)"},
                "structured": {"type": "boolean", "default": False, "description": "返回 JSON 结构化数据"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="get_arp_table",
        description="获取 eNSP 设备 ARP 表。可选指定接口名称过滤。structured=true 返回 JSON。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "interface_name": {"type": "string", "description": "接口名称过滤(可选)"},
                "structured": {"type": "boolean", "default": False, "description": "返回 JSON 结构化数据"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="ping_from_device",
        description=(
            "从 eNSP 设备发起 Ping 测试。\n"
            "device_ip 为源设备,target_ip 为目标,count 为次数,source_ip 可选指定源地址。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "target_ip": {"type": "string", "description": "Ping 目标 IP"},
                "count": {"type": "integer", "default": 5, "description": "Ping 次数"},
                "source_ip": {"type": "string", "description": "指定源 IP(可选)"},
            },
            "required": ["device_ip", "target_ip"],
        },
    ),
    Tool(
        name="traceroute_from_device",
        description="从 eNSP 设备发起 Traceroute 路径追踪。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "target_ip": {"type": "string", "description": "目标 IP"},
                "source_ip": {"type": "string", "description": "指定源 IP(可选)"},
            },
            "required": ["device_ip", "target_ip"],
        },
    ),
    Tool(
        name="get_interface_statistics",
        description="获取 eNSP 设备指定接口的流量统计和错误计数器。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "interface_name": {"type": "string", "description": "接口名称，如 GigabitEthernet0/0/0"},
            },
            "required": ["device_ip", "interface_name"],
        },
    ),

    # --- 协议排错工具 ---
    Tool(
        name="check_ospf",
        description=(
            "检查 eNSP 设备 OSPF 状态:邻居表、接口、LSDB。\n"
            "可选指定 process_id(OSPF 进程号)。structured=true 返回 JSON。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "process_id": {"type": "integer", "description": "OSPF 进程号(可选)"},
                "structured": {"type": "boolean", "default": False, "description": "返回 JSON 结构化数据"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="check_bgp",
        description=(
            "检查 eNSP 设备 BGP 状态:邻居概要、路由表。\n"
            "可选指定 peer_ip 查看特定对等体详细信息。structured=true 返回 JSON。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "peer_ip": {"type": "string", "description": "对等体 IP(可选)"},
                "structured": {"type": "boolean", "default": False, "description": "返回 JSON 结构化数据"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="check_acl",
        description=(
            "检查 eNSP 设备 ACL 规则和匹配统计。\n"
            "可选指定 acl_number(编号或名称),不指定则显示所有 ACL。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "acl_number": {"type": "string", "description": "ACL 编号或名称(可选)"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="check_nat",
        description="检查 eNSP 设备 NAT 状态:会话表、地址池、统计信息。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="check_stp",
        description="检查 eNSP 设备 STP/RSTP/MSTP 生成树状态。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="check_vlan",
        description="检查 eNSP 设备 VLAN 配置和端口分配。可选指定 vlan_id。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "vlan_id": {"type": "integer", "description": "VLAN ID(可选)"},
            },
            "required": ["device_ip"],
        },
    ),

    # --- 设备管理工具 ---
    Tool(
        name="get_device_version",
        description="获取 eNSP 设备版本和硬件信息(型号、软件版本、运行时间等)。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="backup_config",
        description="备份 eNSP 设备当前运行配置,返回完整配置文本。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="compare_config",
        description="对比 eNSP 设备当前运行配置与已保存配置的差异(unified diff 格式)。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="reboot_device",
        description=(
            "重启 eNSP 网络设备(危险操作,会导致短暂中断)。\n"
            "save_before_reboot 为 true 时会在重启前自动保存配置。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "save_before_reboot": {
                    "type": "boolean",
                    "default": True,
                    "description": "重启前是否保存配置",
                },
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="get_log",
        description="获取 eNSP 设备日志信息(logbuffer)。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="get_cpu_memory",
        description="获取 eNSP 设备 CPU 和内存使用情况。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
            },
            "required": ["device_ip"],
        },
    ),

    # --- 拓扑发现工具 ---
    Tool(
        name="discover_topology",
        description=(
            "解析 eNSP .topo 文件，提取拓扑信息（设备列表、接口、链路连接关系）。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": ".topo 文件的完整路径"},
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="find_topo_files",
        description="搜索 eNSP .topo 拓扑文件。不指定目录则搜索桌面/文档等常见位置。",
        inputSchema={
            "type": "object",
            "properties": {
                "search_dir": {"type": "string", "description": "搜索目录(可选)"},
            },
        },
    ),

    # --- 健康检查工具 ---
    Tool(
        name="health_check",
        description="单台 eNSP 设备健康检查：TCP 端口检测 + 轻量命令测试。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="multi_health_check",
        description="批量健康检查。不指定设备则检查所有已注册设备。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "设备名称列表(可选,为空则检查所有)",
                },
            },
        },
    ),

    # --- 配置回滚工具 ---
    Tool(
        name="save_config_snapshot",
        description="保存设备配置快照到本地，便于后续回滚。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "label": {"type": "string", "description": "快照标签(可选)"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="list_config_snapshots",
        description="列出配置快照。可选指定设备名过滤。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称(可选,过滤)"},
            },
        },
    ),
    Tool(
        name="rollback_config",
        description="从快照回滚设备配置。先用 list_config_snapshots 查看可用快照。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称（如 R1）或 IP 地址"},
                "snapshot_filename": {"type": "string", "description": "快照文件名"},
            },
            "required": ["device_ip", "snapshot_filename"],
        },
    ),
]


# ==================== 分发字典 ====================

def _dispatch_register_device(args):
    return register_device(
        args["name"], args["port"],
        username=args.get("username", ""),
        password=args.get("password", ""),
    )

def _dispatch_unregister_device(args):
    return unregister_device(args["name"])

def _dispatch_list_devices(args):
    return list_devices()

def _dispatch_auto_discover(args):
    return auto_discover_devices()

def _dispatch_execute_cli(args):
    return execute_cli(args["device_ip"], args["command"])

def _dispatch_push_config(args):
    return push_config(args["device_ip"], args["commands"])

def _dispatch_save_config(args):
    return save_config(args["device_ip"])

def _dispatch_batch_config(args):
    return batch_config(args["device_ip"], args["config_list"])

def _dispatch_multi_device_push_config(args):
    return multi_device_push_config(args["devices"])

def _dispatch_get_running_config(args):
    return get_running_config(args["device_ip"], args.get("section"))

def _dispatch_get_interface_info(args):
    return get_interface_info(
        args["device_ip"], args.get("interface_name"),
        structured=args.get("structured", False),
    )

def _dispatch_get_routing_table(args):
    return get_routing_table(
        args["device_ip"], args.get("protocol"), args.get("destination"),
        structured=args.get("structured", False),
    )

def _dispatch_get_arp_table(args):
    return get_arp_table(
        args["device_ip"], args.get("interface_name"),
        structured=args.get("structured", False),
    )

def _dispatch_ping(args):
    return ping_from_device(
        args["device_ip"], args["target_ip"],
        args.get("count", 5), source_ip=args.get("source_ip"),
    )

def _dispatch_traceroute(args):
    return traceroute_from_device(
        args["device_ip"], args["target_ip"],
        source_ip=args.get("source_ip"),
    )

def _dispatch_get_interface_statistics(args):
    return get_interface_statistics(args["device_ip"], args["interface_name"])

def _dispatch_check_ospf(args):
    return check_ospf_neighbors(
        args["device_ip"], args.get("process_id"),
        structured=args.get("structured", False),
    )

def _dispatch_check_bgp(args):
    return check_bgp_neighbors(
        args["device_ip"], peer_ip=args.get("peer_ip"),
        structured=args.get("structured", False),
    )

def _dispatch_check_acl(args):
    return check_acl(args["device_ip"], args.get("acl_number"))

def _dispatch_check_nat(args):
    return check_nat(args["device_ip"])

def _dispatch_check_stp(args):
    return check_stp(args["device_ip"])

def _dispatch_check_vlan(args):
    return check_vlan(args["device_ip"], args.get("vlan_id"))

def _dispatch_get_device_version(args):
    return get_device_version(args["device_ip"])

def _dispatch_backup_config(args):
    return backup_config(args["device_ip"])

def _dispatch_compare_config(args):
    return compare_config(args["device_ip"])

def _dispatch_reboot_device(args):
    return reboot_device(args["device_ip"], args.get("save_before_reboot", True))

def _dispatch_get_log(args):
    return get_log_info(args["device_ip"])

def _dispatch_get_cpu_memory(args):
    return get_cpu_memory(args["device_ip"])

def _dispatch_discover_topology(args):
    return discover_topology(args["file_path"])

def _dispatch_find_topo_files(args):
    return find_topo_files(args.get("search_dir", ""))

def _dispatch_health_check(args):
    return health_check(args["device_ip"])

def _dispatch_multi_health_check(args):
    return multi_health_check(args.get("device_names"))

def _dispatch_save_config_snapshot(args):
    return save_config_snapshot(args["device_ip"], args.get("label", ""))

def _dispatch_list_config_snapshots(args):
    return list_config_snapshots(args.get("device_ip", ""))

def _dispatch_rollback_config(args):
    return rollback_config(args["device_ip"], args["snapshot_filename"])


TOOL_DISPATCH = {
    "register_device": _dispatch_register_device,
    "unregister_device": _dispatch_unregister_device,
    "list_devices": _dispatch_list_devices,
    "auto_discover": _dispatch_auto_discover,
    "execute_cli": _dispatch_execute_cli,
    "push_config": _dispatch_push_config,
    "save_config": _dispatch_save_config,
    "batch_config": _dispatch_batch_config,
    "multi_device_push_config": _dispatch_multi_device_push_config,
    "get_running_config": _dispatch_get_running_config,
    "get_interface_info": _dispatch_get_interface_info,
    "get_routing_table": _dispatch_get_routing_table,
    "get_arp_table": _dispatch_get_arp_table,
    "ping_from_device": _dispatch_ping,
    "traceroute_from_device": _dispatch_traceroute,
    "get_interface_statistics": _dispatch_get_interface_statistics,
    "check_ospf": _dispatch_check_ospf,
    "check_bgp": _dispatch_check_bgp,
    "check_acl": _dispatch_check_acl,
    "check_nat": _dispatch_check_nat,
    "check_stp": _dispatch_check_stp,
    "check_vlan": _dispatch_check_vlan,
    "get_device_version": _dispatch_get_device_version,
    "backup_config": _dispatch_backup_config,
    "compare_config": _dispatch_compare_config,
    "reboot_device": _dispatch_reboot_device,
    "get_log": _dispatch_get_log,
    "get_cpu_memory": _dispatch_get_cpu_memory,
    "discover_topology": _dispatch_discover_topology,
    "find_topo_files": _dispatch_find_topo_files,
    "health_check": _dispatch_health_check,
    "multi_health_check": _dispatch_multi_health_check,
    "save_config_snapshot": _dispatch_save_config_snapshot,
    "list_config_snapshots": _dispatch_list_config_snapshots,
    "rollback_config": _dispatch_rollback_config,
}


# ==================== 工具列表处理器 ====================

@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """返回所有可用工具"""
    return TOOLS


# ==================== 工具调用处理器 ====================

@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    """分发工具调用到对应的实现函数（通过 asyncio.to_thread 包装阻塞调用）"""
    handler = TOOL_DISPATCH.get(name)
    if handler is None:
        return [TextContent(type="text", text=f"未知工具: {name}")]

    try:
        result = await asyncio.to_thread(handler, arguments)
    except Exception as e:
        logger.error("工具 %s 执行异常: %s", name, e, exc_info=True)
        result = f"工具 {name} 执行失败: {str(e)}"

    return [TextContent(type="text", text=result)]


# ==================== 启动入口 ====================

async def run():
    """以 stdio 模式启动 MCP Server"""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
