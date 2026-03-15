"""
eNSP MCP Server 入口

精简版：15 个工具，使用 HuaweiConsole 直连 eNSP 控制台。
"""
import asyncio
import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .config import config, register_device, unregister_device, list_devices, auto_discover_devices
from .tools.cli import (
    execute_cli, push_config, multi_device_push_config,
    save_config, get_running_config,
    ping_from_device, traceroute_from_device,
    health_check, multi_health_check,
)
from .tools.topology import discover_topology, find_topo_files

logger = logging.getLogger("ensp_mcp.server")

server = Server(config.server_name)

# ==================== 工具定义（15 个） ====================

TOOLS = [
    # --- 设备注册 ---
    Tool(
        name="register_device",
        description=(
            "注册 eNSP 设备。提供设备名称和 eNSP 分配的本地 TCP 端口号。\n"
            "注册后即可用设备名(如 R1、SW1)代替 IP 地址来操作设备。\n"
            "端口号可在 eNSP 中右键设备 → 设置 → 串口号 查看。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "设备名称,如 R1, R2, SW1"},
                "port": {"type": "integer", "description": "eNSP 本地 TCP 端口号,如 2000, 2001"},
                "username": {"type": "string", "description": "设备用户名(可选,per-device 覆盖)"},
                "password": {"type": "string", "description": "设备密码(可选,per-device 覆盖)"},
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
            "自动发现 eNSP 设备。扫描 eNSP 进程监听的控制台端口,"
            "连接每台设备获取主机名,合并更新设备注册表(保留手动注册的设备)。"
        ),
        inputSchema={"type": "object", "properties": {}},
    ),

    # --- CLI 核心 ---
    Tool(
        name="execute_cli",
        description=(
            "在指定的 eNSP 网络设备上执行单条查询/显示命令,用于排错。\n"
            "例如:display ip interface brief, display ospf peer, display vlan 等。\n"
            "所有 display 类命令都可通过此工具执行。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称(如 R1)或 IP 地址"},
                "command": {"type": "string", "description": "要执行的命令"},
            },
            "required": ["device_ip", "command"],
        },
    ),
    Tool(
        name="push_config",
        description=(
            "向 eNSP 网络设备下发配置。\n"
            "传入的是一个配置命令的列表,例如:['vlan 10', 'interface g0/0/1', 'port link-type access']\n"
            "会自动进入 system-view 并在执行完后 return 退出。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称(如 R1)或 IP 地址"},
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
    Tool(
        name="save_config",
        description="保存 eNSP 网络设备的当前配置(执行 save,自动处理 [Y/N] 交互确认)。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称(如 R1)或 IP 地址"},
            },
            "required": ["device_ip"],
        },
    ),
    Tool(
        name="get_running_config",
        description=(
            "获取 eNSP 设备的当前运行配置。\n"
            '可选指定 section 过滤配置段落(如 "interface", "ospf", "bgp"),不传则返回全部配置。'
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称(如 R1)或 IP 地址"},
                "section": {"type": "string", "description": "过滤配置段落(可选)"},
            },
            "required": ["device_ip"],
        },
    ),

    # --- 诊断 ---
    Tool(
        name="ping_from_device",
        description=(
            "从 eNSP 设备发起 Ping 测试。\n"
            "device_ip 为源设备,target_ip 为目标,count 为次数,source_ip 可选指定源地址。"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称(如 R1)或 IP 地址"},
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
                "device_ip": {"type": "string", "description": "设备名称(如 R1)或 IP 地址"},
                "target_ip": {"type": "string", "description": "目标 IP"},
                "source_ip": {"type": "string", "description": "指定源 IP(可选)"},
            },
            "required": ["device_ip", "target_ip"],
        },
    ),

    # --- 健康检查 ---
    Tool(
        name="health_check",
        description="单台 eNSP 设备健康检查:TCP 端口检测 + 轻量命令测试。",
        inputSchema={
            "type": "object",
            "properties": {
                "device_ip": {"type": "string", "description": "设备名称(如 R1)或 IP 地址"},
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

    # --- 拓扑 ---
    Tool(
        name="discover_topology",
        description="解析 eNSP .topo 文件,提取拓扑信息(设备列表、接口、链路连接关系)。",
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
]


# ==================== 分发字典 ====================

TOOL_DISPATCH = {
    "register_device": lambda a: register_device(
        a["name"], a["port"], username=a.get("username", ""), password=a.get("password", ""),
    ),
    "unregister_device": lambda a: unregister_device(a["name"]),
    "list_devices": lambda a: list_devices(),
    "auto_discover": lambda a: auto_discover_devices(),
    "execute_cli": lambda a: execute_cli(a["device_ip"], a["command"]),
    "push_config": lambda a: push_config(a["device_ip"], a["commands"]),
    "multi_device_push_config": lambda a: multi_device_push_config(a["devices"]),
    "save_config": lambda a: save_config(a["device_ip"]),
    "get_running_config": lambda a: get_running_config(a["device_ip"], a.get("section")),
    "ping_from_device": lambda a: ping_from_device(
        a["device_ip"], a["target_ip"], a.get("count", 5), source_ip=a.get("source_ip"),
    ),
    "traceroute_from_device": lambda a: traceroute_from_device(
        a["device_ip"], a["target_ip"], source_ip=a.get("source_ip"),
    ),
    "health_check": lambda a: health_check(a["device_ip"]),
    "multi_health_check": lambda a: multi_health_check(a.get("device_names")),
    "discover_topology": lambda a: discover_topology(a["file_path"]),
    "find_topo_files": lambda a: find_topo_files(a.get("search_dir", "")),
}


# ==================== MCP 处理器 ====================

@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    handler = TOOL_DISPATCH.get(name)
    if handler is None:
        return [TextContent(type="text", text=f"未知工具: {name}")]

    try:
        result = await asyncio.to_thread(handler, arguments)
    except Exception as e:
        logger.error("工具 %s 执行异常: %s", name, e, exc_info=True)
        result = f"工具 {name} 执行失败: {e}"

    return [TextContent(type="text", text=result)]


# ==================== 启动入口 ====================

async def run():
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
