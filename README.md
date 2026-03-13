# eNSP MCP Server

让 AI 通过 MCP 协议管理华为 eNSP 模拟网络设备。

支持 Claude Desktop、Claude Code、Cursor 等任何 MCP 客户端，提供 **35 个工具**，覆盖 CLI 执行、配置管理、协议排错、网络诊断、拓扑发现、健康检查、配置回滚等功能。

## 前置条件

- Windows 系统（eNSP 仅支持 Windows）
- [华为 eNSP](https://support.huawei.com/enterprise/zh/tool/ensp-TL1000000015) 已安装并启动
- Python 3.10+
- 一个支持 MCP 的 AI 客户端（Claude Desktop / Claude Code / Cursor 等）

## 安装

```bash
git clone https://github.com/Redmig110/eNSP-AI.git
cd eNSP-AI
pip install -r requirements.txt
```

## 配置 MCP 客户端

### Claude Code

```bash
claude mcp add ensp-ai -- python -m ensp_mcp_server.server --cwd /path/to/eNSP-AI
```

或手动编辑 `~/.claude.json`，在 `mcpServers` 中添加：

```json
{
  "ensp-ai": {
    "command": "python",
    "args": ["-m", "ensp_mcp_server.server"],
    "cwd": "A:\\eNSP-AI"
  }
}
```

### Claude Desktop

编辑 `%APPDATA%\Claude\claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "ensp-ai": {
      "command": "python",
      "args": ["-m", "ensp_mcp_server.server"],
      "cwd": "A:\\eNSP-AI"
    }
  }
}
```

### Cursor

在 Settings → MCP Servers 中添加，配置同上。

> **注意：** `cwd` 路径改成你实际的项目目录。

## 使用方式

### 1. 启动 eNSP 并运行设备

在 eNSP 中打开拓扑，启动所有设备，等待设备完成初始化。

### 2. 注册设备

有两种方式：

**自动发现（推荐）：** 直接让 AI 执行 `auto_discover`，自动扫描 eNSP 进程端口并注册所有设备。

**手动注册：** 在 eNSP 中右键设备 → 设置 → 查看串口号，然后让 AI 执行：
```
register_device(name="R1", port=2000)
```

### 3. 开始使用

注册完成后，直接用自然语言和 AI 对话即可：

```
"帮我看看 R1 的路由表"
"给 R1 和 R2 配置 OSPF"
"检查所有设备的健康状态"
"对比 R1 当前配置和保存的配置有什么差异"
"保存 R1 的配置快照，然后修改 OSPF，如果有问题就回滚"
```

## 工具列表

### 设备管理（4 个）
| 工具 | 说明 |
|------|------|
| `register_device` | 注册 eNSP 设备（名称 + 端口） |
| `unregister_device` | 注销设备 |
| `list_devices` | 列出所有已注册设备 |
| `auto_discover` | 自动发现并注册 eNSP 设备 |

### CLI 操作（5 个）
| 工具 | 说明 |
|------|------|
| `execute_cli` | 执行单条查询命令 |
| `push_config` | 下发配置命令列表 |
| `save_config` | 保存配置（自动处理 Y/N 确认） |
| `batch_config` | 批量下发多组配置 |
| `multi_device_push_config` | 同时向多台设备下发配置 |

### 网络诊断（7 个）
| 工具 | 说明 |
|------|------|
| `get_running_config` | 查看运行配置（支持 section 过滤） |
| `get_interface_info` | 查看接口信息（支持 JSON 输出） |
| `get_routing_table` | 查看路由表（支持协议过滤 + JSON） |
| `get_arp_table` | 查看 ARP 表（支持 JSON 输出） |
| `ping_from_device` | 从设备发起 Ping |
| `traceroute_from_device` | 从设备发起 Traceroute |
| `get_interface_statistics` | 接口流量统计和错误计数 |

### 协议排错（6 个）
| 工具 | 说明 |
|------|------|
| `check_ospf` | OSPF 邻居/接口/LSDB（支持 JSON） |
| `check_bgp` | BGP 邻居/路由表（支持 JSON） |
| `check_acl` | ACL 规则和匹配统计 |
| `check_nat` | NAT 会话/地址池/统计 |
| `check_stp` | STP/RSTP/MSTP 状态 |
| `check_vlan` | VLAN 配置和端口分配 |

### 设备运维（6 个）
| 工具 | 说明 |
|------|------|
| `get_device_version` | 设备版本和硬件信息 |
| `backup_config` | 备份完整配置 |
| `compare_config` | 对比运行配置与保存配置（diff 格式） |
| `reboot_device` | 重启设备（可选保存） |
| `get_log` | 查看设备日志 |
| `get_cpu_memory` | CPU 和内存使用情况 |

### 拓扑发现（2 个）
| 工具 | 说明 |
|------|------|
| `discover_topology` | 解析 eNSP .topo 文件 |
| `find_topo_files` | 搜索本地 .topo 文件 |

### 健康检查（2 个）
| 工具 | 说明 |
|------|------|
| `health_check` | 单设备健康检查（TCP + CLI） |
| `multi_health_check` | 批量健康检查 |

### 配置回滚（3 个）
| 工具 | 说明 |
|------|------|
| `save_config_snapshot` | 保存配置快照到本地 |
| `list_config_snapshots` | 列出可用快照 |
| `rollback_config` | 从快照回滚配置 |

## 环境变量（可选）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ENSP_USERNAME` | 全局设备用户名 | 空 |
| `ENSP_PASSWORD` | 全局设备密码 | 空 |
| `ENSP_LOG_LEVEL` | 日志级别（DEBUG/INFO/WARNING/ERROR） | WARNING |

## 项目结构

```
eNSP-AI/
├── requirements.txt          # 依赖
└── ensp_mcp_server/          # 主包
    ├── __init__.py            # 版本号
    ├── server.py              # MCP Server 入口
    ├── config.py              # 配置 & 设备注册表
    ├── exceptions.py          # 自定义异常
    ├── connection_pool.py     # 连接池
    ├── parsers.py             # 结构化输出解析器
    └── tools/
        ├── cli.py             # CLI 执行工具
        ├── diagnostic.py      # 网络诊断工具
        ├── protocol.py        # 协议排错工具
        ├── management.py      # 设备管理 & 配置回滚
        ├── topology.py        # 拓扑发现
        └── health.py          # 健康检查
```

## License

MIT
