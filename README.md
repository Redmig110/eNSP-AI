# eNSP-AI

让 AI 通过 MCP 协议管理华为 eNSP 模拟网络设备，并提供拓扑可视化工具。

## 功能概览

- **MCP Server** — 通过自然语言操作 eNSP 设备（CLI 执行、配置下发、网络诊断、拓扑发现、健康检查、实验验收与巡检）
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

仓库根目录已经带了 `.mcp.json`，**clone 后直接在项目里打开 Claude Code 即可**。首次会提示是否信任该 MCP server，确认后就能用。

`.mcp.json` 里刻意没有 `cwd` 字段 —— 项目级配置的工作目录自动取项目目录。这点必须对：服务是以 `python -m ensp_mcp_server.server` 启动的，`-m` 从工作目录解析包，工作目录不对会直接报 `ModuleNotFoundError: No module named 'ensp_mcp_server'`。

> **不要用 `claude mcp add ... --cwd <路径>`。** `claude mcp add` 没有指定工作目录的选项，而 `server.py` 的 `main()` 不解析 argv —— `--cwd` 会被当成参数传给服务端然后静默丢弃，工作目录根本没生效。

### Claude Desktop

编辑 `%APPDATA%\Claude\claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "ensp-ai": {
      "command": "python",
      "args": ["-m", "ensp_mcp_server.server"],
      "cwd": "C:/path/to/eNSP-AI"
    }
  }
}
```

### Cursor

在 Settings > MCP Servers 中添加，配置同上。

> **注意：** Claude Desktop / Cursor 不读取项目级 `.mcp.json`，所以这里**必须显式写 `cwd`** 并改成你实际的项目目录 —— 且它必须是**仓库根目录**，否则包导入会失败。

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

- 解析 eNSP `.topo` XML，渲染设备节点 + 连线 + 接口名
- 渲染 eNSP 里的文本标注（`txttip`，通常写作网段、VLAN、机房名等说明）
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
> 注意此时仍需要联网加载 CDN 上的 vis-network，否则图画不出来。

### .topo 文件的两个坑

写别的解析器时要注意（`topo-viewer.html` 和 MCP 的 `discover_topology` 都已处理）：

1. **编码**：文件头声明 `encoding="UNICODE"`，但实际写的是 **GBK**。
   Python 的 `ET.parse()` 会直接抛 `LookupError: unknown encoding: UNICODE`；
   浏览器不处理的话中文设备名/标注会变成 ``。
2. **标签名**：真实结构是 `devices/dev`、`lines/line/interfacePair`、`txttips/txttip`，
   不是 `<device>` / `<node>`。另外**一条 `<line>` 可以挂多个 `<interfacePair>`**（多段链路），
   只取第一个会漏链路。

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

### eNSP 应用与拓扑生命周期
| 工具 | 说明 |
|------|------|
| `open_ensp` | 启动 eNSP 界面（已在运行则不重复启动） |
| `close_ensp` | 优雅关闭 eNSP（发 WM_CLOSE，不 taskkill） |
| `open_topology` | 打开指定 .topo 文件 |
| `close_topology` | 关闭当前拓扑页签，eNSP 保持运行 |

这四个工具用 stdlib `ctypes` 直接调 Win32，**不依赖 pywin32**。exe 路径按
`ENSP_EXE` 环境变量 → 注册表 `HKCR\topofile\shell\open\command` → 常见安装目录的顺序查找。

#### 实测出来的三个限制（用之前先读，别踩）

1. **`open_topology` 只在 eNSP 没运行时可靠。** eNSP 已在运行时，命令行把 `.topo`
   转交给它的这条路基本走不通：已有非空拓扑时**完全无效**，一个画布都没有时**超时无效**，
   只有"运行中 + 空白板"成功过一次（未复现）。此时工具会**如实报失败**并给出替代做法
   （GUI 里 Ctrl+O，或先 `close_ensp` 再打开）。
   > 成功判据是**交接前后画布状态有没有变化**，不是"画布上有没有设备标签"。
   > 后者在 eNSP 已开着别的拓扑时恒为真，会把「根本没打开」静默误报成成功。
2. **`close_ensp` 只在没有打开拓扑时才干脆**（1~5 秒退出）。只要 eNSP 里开着拓扑，
   关闭就会弹模态确认框把 `WM_CLOSE` 挡住，工具会报「未退出」并**指出那个对话框的窗口句柄**。
   工具**不替你点按钮、不 taskkill**（这是有意的设计选择）。
3. **残留进程是 eNSP 自身行为**：关掉拓扑或关掉 eNSP 之后，`eNSP_Switch.exe` /
   `VBoxHeadless.exe` 可能残留且控制台端口消失（"进程还在但 MCP 连不上"的僵尸态）；
   `open_topology` 失败时还可能残留一个没有窗口的启动器进程。
   工具**只报告、不清理**。

另外：载入 `.topo` 只是画出拓扑，**设备不会自动启动**，需要在 GUI 里点「启动设备」
（Ctrl+Alt+A）；eNSP 的 Ctrl+S 保存的是拓扑，**不是**设备配置。

### 巡检与验收
| 工具 | 说明 |
|------|------|
| `verify_expectations` | 跑一组「命令 + 预期」逐条判定通过/不通过，用于实验验收 |
| `inspect_topology` | 通用体检：可达性、异常接口、日志报错、配置是否已保存 |

**实验验收**：给一组检查，每条写明要跑的命令和预期。检查可以内联传，也可以写成 JSON 清单文件反复使用：

```json
{
  "checks": [
    { "device": "LSW1", "command": "display vrrp",
      "desc": "VLAN10 网关优先级没被改掉",
      "expect_contains": ["PriorityConfig : 120"] },
    { "device": "LSW1", "command": "display ospf peer",
      "desc": "OSPF 邻接应全为 Full",
      "expect_match": "\\bFull\\b" }
  ]
}
```

支持三种预期：`expect_contains`（这些子串都要出现）、`expect_not_contains`（都不能出现）、`expect_match`（正则至少匹配一次）。写了预期才算判定；三条都没写的检查记 `INFO`，只贴回显不判定。

> **数值判据请加词边界**，写成 `\b120\b`。不加的话 `120` 会前缀命中 `1200`，静默误判成通过。

**通用巡检**不需要写任何预期，直接跑即可。

## 项目结构

```
eNSP-AI/
├── requirements.txt          # Python 依赖
├── .mcp.json                 # Claude Code 项目级 MCP 配置
├── CLAUDE.md                 # 给 AI agent 的项目说明
├── docs/                     # 项目文档：git.md 提交规范、agents/ agent 约定
├── logs/                     # 项目日志：dev/ 开发、lab/ 实验、troubleshoot/ 排障
├── tests/                    # 离线测试（不需要 eNSP 设备）
├── topo-viewer.html          # 拓扑可视化（浏览器）
├── topo-server.py            # 可视化本地服务器
└── ensp_mcp_server/          # MCP Server 主包
    ├── __init__.py
    ├── server.py              # MCP Server 入口 & 工具注册
    ├── config.py              # 配置 & 设备注册表
    ├── console.py             # 设备控制台连接（原生 socket）
    ├── exceptions.py          # 自定义异常
    └── tools/
        ├── cli.py             # CLI 执行 & 配置工具
        ├── ensp_app.py        # eNSP 应用与拓扑生命周期（Win32 ctypes）
        ├── inspection.py      # 巡检 & 实验验收
        └── topology.py        # .topo 解析 & 搜索
```

## 环境变量（可选）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ENSP_USERNAME` | 全局设备用户名 | 空 |
| `ENSP_PASSWORD` | 全局设备密码 | 空 |
| `ENSP_LOG_LEVEL` | 日志级别 | WARNING |
| `ENSP_EXE` | eNSP_Client.exe 完整路径，覆盖自动探测 | 自动探测 |

## License

MIT
