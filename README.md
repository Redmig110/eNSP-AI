# eNSP-AI

让 AI 通过 MCP 协议管理华为 eNSP 模拟网络设备，并提供拓扑可视化工具。

## 功能概览

- **MCP Server** — 通过自然语言操作 eNSP 设备（CLI 执行、配置下发、网络诊断、拓扑发现、健康检查）
- **Topo Viewer** — 浏览器拓扑可视化，拖入 .topo 文件即可渲染交互式网络图，自动同步拓扑数据给 AI

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

在 Settings > MCP Servers 中添加，配置同上。

> **注意：** `cwd` 路径改成你实际的项目目录。

## 使用方式

### 1. 启动 eNSP 并运行设备

在 eNSP 中打开拓扑，启动所有设备，等待设备完成初始化。

### 2. 注册设备

有两种方式：

**自动发现（推荐）：** 直接让 AI 执行 `auto_discover`，自动扫描 eNSP 进程端口并注册所有设备。

**手动注册：** 在 eNSP 中右键设备 > 设置 > 查看串口号，然后让 AI 执行：
```
register_device(name="R1", port=2000)
```

### 3. 开始使用

注册完成后，直接用自然语言和 AI 对话即可：

```
"帮我看看 R1 的路由表"
"给 R1 和 R2 配置 OSPF"
"检查所有设备的健康状态"
```

## Topo Viewer 拓扑可视化

浏览器拓扑查看器，拖入 .topo 文件即可渲染交互式网络图，并自动同步拓扑数据给 AI。

### 启动

```bash
cd eNSP-AI
python topo-server.py
```

浏览器自动打开，拖入 .topo 文件即可。AI 通过读取自动生成的 `topology.json` 获取拓扑信息。

### 功能

- 解析 eNSP `.topo` XML，渲染设备节点 + 连线 + 接口名 + IP 标注
- 设备图标区分：路由器 / 交换机 / 防火墙 / PC / Cloud
- 接口索引自动还原为接口名（交换机 1-based，路由器 0-based）
- 粘贴截图 (Ctrl+V) 或拖入图片作为参考底图，支持透明度调节
- 点击设备查看详情（型号、COM 端口、接口列表）
- 底部状态栏分类统计，左上角设备图例
- 快捷键：`F` 适应画布、`Esc` 关闭面板、`Del` 清除
- 拓扑数据自动同步到 `topology.json`，AI 可直接读取

### 数据流

```
拖入 .topo --> 浏览器解析渲染 --> POST /api/topology --> topology.json 落盘
                                                              |
                                                    AI 读取此文件进行配置
```

> 也可双击 `topo-viewer.html` 独立使用（不启动服务器时可视化仍可用，仅同步功能不生效）。

## MCP 工具列表

### 设备管理
| 工具 | 说明 |
|------|------|
| `register_device` | 注册 eNSP 设备（名称 + 端口） |
| `unregister_device` | 注销设备 |
| `list_devices` | 列出所有已注册设备 |
| `auto_discover` | 自动发现并注册 eNSP 设备 |

### CLI 与配置
| 工具 | 说明 |
|------|------|
| `execute_cli` | 执行单条查询/显示命令 |
| `push_config` | 下发配置命令列表（自动进入 system-view） |
| `multi_device_push_config` | 同时向多台设备下发配置 |
| `save_config` | 保存配置（自动处理 Y/N 确认） |
| `get_running_config` | 查看运行配置（支持 section 过滤） |

### 网络诊断
| 工具 | 说明 |
|------|------|
| `ping_from_device` | 从设备发起 Ping |
| `traceroute_from_device` | 从设备发起 Traceroute |
| `health_check` | 单设备健康检查（TCP + CLI） |
| `multi_health_check` | 批量健康检查 |

### 拓扑发现
| 工具 | 说明 |
|------|------|
| `discover_topology` | 解析 eNSP .topo 文件 |
| `find_topo_files` | 搜索本地 .topo 文件 |

## 项目结构

```
eNSP-AI/
├── requirements.txt          # Python 依赖
├── topo-viewer.html          # 拓扑可视化（浏览器）
├── topo-server.py            # 可视化本地服务器
└── ensp_mcp_server/          # MCP Server 主包
    ├── __init__.py
    ├── server.py              # MCP Server 入口 & 工具注册
    ├── config.py              # 配置 & 设备注册表
    ├── console.py             # Telnet 控制台连接
    ├── exceptions.py          # 自定义异常
    └── tools/
        └── cli.py             # CLI 执行 & 配置工具
```

## 环境变量（可选）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ENSP_USERNAME` | 全局设备用户名 | 空 |
| `ENSP_PASSWORD` | 全局设备密码 | 空 |
| `ENSP_LOG_LEVEL` | 日志级别 | WARNING |

## License

MIT
