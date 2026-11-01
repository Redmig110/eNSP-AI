# eNSP-AI

让 AI 通过 MCP 协议直接操作华为 eNSP 模拟设备：执行命令、下发配置、诊断网络、验收实验。另附一个浏览器拓扑查看器。

**平台** Windows · **Python** 3.10+ · **协议** MCP (stdio) · **许可** MIT

## 目录

- [它解决什么问题](#它解决什么问题)
- [功能一览](#功能一览)
- [快速开始](#快速开始)
- [前置条件](#前置条件)
- [安装](#安装)
- [配置 MCP 客户端](#配置-mcp-客户端)
- [使用指南](#使用指南)
  - [设备注册](#设备注册)
  - [devices.json](#devicesjson)
  - [典型工作流](#典型工作流)
  - [工具参考](#工具参考)
- [Topo Viewer](#topo-viewer)
- [工作原理](#工作原理)
- [已知限制](#已知限制)
- [故障排查](#故障排查)
- [开发](#开发)
- [环境变量](#环境变量)
- [来源与致谢](#来源与致谢)
- [License](#license)

## 它解决什么问题

eNSP 只有图形界面：批量下发配置、逐台巡检、验证某一层是否真的生效，都只能手点。

本项目把 eNSP 设备的控制台端口封装成 21 个 MCP 工具，AI 可以直接：

- 执行 `display` 命令、下发配置、保存配置；
- 从设备侧发起 ping / traceroute；
- 解析 `.topo` 拓扑；
- 用「命令 + 预期」批量验收实验；
- 做通用体检（可达性、异常接口、日志、配置是否保存）。

**它不是自动化黑盒。** 下发的仍是标准 VRP 命令，回显原样返回；AI 的判断依据是设备上的真实表项，而不是模拟结果。

## 功能一览

- **MCP Server** —— 21 个工具，覆盖设备注册、CLI/配置、诊断、拓扑、eNSP 生命周期、验收巡检。
- **Topo Viewer** —— 浏览器里拖入 `.topo` 即可渲染设备、链路、接口与文本标注，并把拓扑落盘成 `topology.json`。
- **离线测试** —— 不需要 eNSP 设备，直接跑两个只用标准库的测试脚本。

## 快速开始

1. **准备环境**：Windows + 已安装 eNSP + Python 3.10+。
2. **获取代码并安装依赖**：

   ```bash
   git clone https://github.com/Redmig110/eNSP-AI.git
   cd eNSP-AI
   pip install -r requirements.txt
   ```

3. **启动 eNSP 和设备**：在 eNSP 里打开拓扑，点「启动设备」，等设备初始化完成。交换机约 1 分钟，AR 路由器要数分钟。
4. **接入 AI 客户端**：用 Claude Code 的话，直接在仓库目录打开即可（仓库自带 `.mcp.json`）。其他客户端见[配置 MCP 客户端](#配置-mcp-客户端)。
5. **注册设备并试一条命令**：对 AI 说「自动发现设备」，或调用 `auto_discover`；然后说「显示 R1 的路由表」，或调用 `execute_cli(device_ip="R1", command="display ip routing-table")`。

## 前置条件

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10/11（eNSP 仅支持 Windows） |
| eNSP | 已安装且能启动设备；eNSP 依赖 VirtualBox |
| Python | 3.10 及以上 |
| AI 客户端 | 支持 MCP stdio 的客户端（Claude Code / Claude Desktop / Cursor 等） |

> 设备必须先在 eNSP GUI 里启动。MCP 只连接已经起来的控制台端口，**不能**启动设备。

## 安装

```bash
git clone https://github.com/Redmig110/eNSP-AI.git
cd eNSP-AI
pip install -r requirements.txt
```

依赖只有两个：

```
mcp>=1.0.0,<2
pydantic>=2.0.0
```

> **`mcp` 必须小于 2。** `server.py` 用的是 v1 低层 API（`mcp.server.Server` + `@server.list_tools()`）；2.x 已移除该装饰器，启动即报 `AttributeError: 'Server' object has no attribute 'list_tools'`。

本项目没有打包成 pip 包，直接从源码目录运行。

## 配置 MCP 客户端

### Claude Code

仓库根目录已经带 `.mcp.json`，**在项目目录里打开 Claude Code 即可**，首次提示是否信任该 MCP server，确认后就能用。

`.mcp.json` 里刻意没有 `cwd` 字段 —— 项目级配置的工作目录自动取项目目录。服务以 `python -m ensp_mcp_server.server` 启动，`-m` 从当前工作目录解析包，工作目录不对会直接报：

```
ModuleNotFoundError: No module named 'ensp_mcp_server'
```

> 不要用 `claude mcp add ... --cwd <路径>`：`claude mcp add` 没有指定工作目录的选项，`server.py` 的 `main()` 也不解析 argv，`--cwd` 会被静默丢弃。

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

### Cursor 与其他客户端

在 Settings > MCP Servers 里添加，配置同上。关键是 `cwd` 必须是**仓库根目录**。

> Claude Desktop / Cursor 不读取项目级 `.mcp.json`，所以必须显式写 `cwd`。

## 使用指南

### 设备注册

**自动发现（推荐）**

调用 `auto_discover`。它会：

1. 找到 `eNSP_Client.exe` 进程；
2. 扫描该进程监听的 2000–2999 端口；
3. 逐端口连接，读取 `sysname`；
4. 合并进设备注册表（保留手动注册且端口未被发现的设备）。

主机名仍是默认 `Huawei` 的设备会自动命名为 `Device1`、`Device2`……建议先在设备上执行 `sysname R1`，再重新发现。

**手动注册**

在 eNSP 里右键设备 → 设置 → 查看串口号，然后：

```
register_device(name="R1", port=2000)
```

需要登录的设备（如 USG6000V）可以带凭据：

```
register_device(name="FW", port=2001, username="admin", password="...")
```

> 设备重启或端口变化后，`devices.json` 里的旧条目会失效。先 `auto_discover`，不要沿用旧注册表。

### devices.json

注册表保存在仓库根目录的 `devices.json`，两种写法都支持：

```json
{
  "R1": 2000,
  "FW": { "port": 2001, "username": "admin", "password": "..." }
}
```

- `register_device` / `unregister_device` / `auto_discover` 会自动读写该文件。
- 未注册的标识不会被拒绝：会被当成 IP 地址、按 23 端口连接。所以看到连接失败时，先确认名字是否已注册。

> `devices.json` 里的设备密码是**明文**保存的。该文件已被 `.gitignore` 排除，不要提交或分享。

### 典型工作流

**查看状态**

```
execute_cli(device_ip="R1", command="display ip routing-table")
execute_cli(device_ip="LSW1", command="display vlan")
```

**下发配置**

```
push_config(device_ip="R1", commands=[
  "vlan 10",
  "interface Vlanif10",
  "ip address 192.168.10.1 24"
])
```

`push_config` 会自动进入 `system-view`。**执行完不会 `return`，设备留在系统视图**；下一次下发时，如果设备停在 `interface` / `acl` 等子视图，会先 `return` 再重新进系统视图。

**保存配置**

```
save_config(device_ip="R1")
```

会自动回答 `[Y/N]`，并处理部分 S5700 / S3700 追问的 `Please input the file name ...`。改完必须保存，否则重启丢失。

**多设备下发**

```
multi_device_push_config(devices=[
  {"device_ip": "R1", "commands": ["ospf 1", "area 0"]},
  {"device_ip": "R2", "commands": ["ospf 1", "area 0"]}
])
```

**实验验收**

`verify_expectations` 跑一组「命令 + 预期」并逐条判定。检查可以内联传，也可以写成 JSON 清单：

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

三种预期：

- `expect_contains`：这些子串都要出现；
- `expect_not_contains`：这些子串都不能出现；
- `expect_match`：正则至少匹配一次。

三条都没写的检查记 `INFO`，只贴回显不判定。

> 数值判据请加词边界，写成 `\b120\b`。不加的话 `120` 会前缀命中 `1200`，静默误判成通过。

**通用巡检**

`inspect_topology` 不需要写预期，对每台设备体检四项：可达性、「有 IP 但协议 down」的接口、最近日志里值得看的行、当前配置与已保存配置是否一致。

### 工具参考

#### 设备管理

| 工具 | 参数 | 说明 |
|---|---|---|
| `register_device` | `name`, `port`, `username?`, `password?` | 注册设备；注册后可用设备名代替 IP |
| `unregister_device` | `name` | 注销设备 |
| `list_devices` | — | 列出所有已注册设备 |
| `auto_discover` | — | 自动发现并合并注册 eNSP 设备 |

#### CLI 与配置

| 工具 | 参数 | 说明 |
|---|---|---|
| `execute_cli` | `device_ip`, `command` | 执行单条查询/显示命令；`device_ip` 可传设备名 |
| `push_config` | `device_ip`, `commands[]` | 自动进 `system-view`；结束后留在系统视图 |
| `multi_device_push_config` | `devices[{device_ip, commands[]}]` | 同时向多台设备下发配置 |
| `save_config` | `device_ip` | 保存配置，自动处理 Y/N 与文件名提示 |
| `get_running_config` | `device_ip`, `section?` | 查看运行配置，可按 `section` 过滤 |

#### 网络诊断

| 工具 | 参数 | 说明 |
|---|---|---|
| `ping_from_device` | `device_ip`, `target_ip`, `count?=5`, `source_ip?` | 从设备发起 Ping |
| `traceroute_from_device` | `device_ip`, `target_ip`, `source_ip?` | 从设备发起 Traceroute |
| `health_check` | `device_ip` | 单设备健康检查（TCP + 轻量命令） |
| `multi_health_check` | `device_names?` | 批量健康检查，不传则检查全部 |

#### 拓扑

| 工具 | 参数 | 说明 |
|---|---|---|
| `discover_topology` | `file_path` | 解析 `.topo`，提取设备、接口与链路 |
| `find_topo_files` | `search_dir?` | 搜索 `.topo` 文件；默认搜桌面、文档、下载和 eNSP 安装目录 |

#### eNSP 应用与拓扑生命周期

| 工具 | 参数 | 说明 |
|---|---|---|
| `open_ensp` | — | 启动 eNSP 界面；单实例，已在运行则不重复启动 |
| `close_ensp` | — | 优雅关闭 eNSP（发 `WM_CLOSE`，不 `taskkill`） |
| `open_topology` | `file_path` | 打开指定 `.topo` 文件 |
| `close_topology` | — | 关闭当前拓扑页签，eNSP 保持运行 |

#### 验收与巡检

| 工具 | 参数 | 说明 |
|---|---|---|
| `verify_expectations` | `checks?`, `checklist_file?` | 跑一组「命令 + 预期」并逐条判定 |
| `inspect_topology` | `device_names?` | 通用体检：可达性、异常接口、日志、配置是否保存 |

## Topo Viewer

浏览器拓扑查看器。拖入 `.topo` 即可渲染交互式网络图，并把拓扑数据同步给 AI。

### 启动

```bash
cd eNSP-AI
python topo-server.py
```

浏览器会自动打开 `http://127.0.0.1:18080/topo-viewer.html`，拖入 `.topo` 文件即可。

### 功能

- 解析 eNSP `.topo` XML，渲染设备节点 + 连线 + 接口名；
- 渲染 eNSP 里的文本标注（`txttip`，通常写作网段、VLAN、机房名等说明）；
- 设备图标区分：路由器 / 交换机 / 防火墙 / PC / Cloud；
- 接口索引自动还原为接口名（交换机 1-based，路由器等其他设备 0-based）；
- 粘贴截图（Ctrl+V）或拖入图片作为参考底图，支持透明度调节；
- 点击设备查看详情（型号、COM 端口、接口列表）；
- 底部状态栏分类统计，左上角设备图例；
- 快捷键：`F` 适应画布、`Esc` 关闭面板、`Del` 清除；
- 拓扑数据自动同步到 `topology.json`，AI 可直接读取。

### 数据流

```
拖入 .topo --> 浏览器解析渲染 --> POST /api/topology --> topology.json 落盘
                                                              |
                                                    AI 读取此文件进行配置
```

> 也可以双击 `topo-viewer.html` 独立使用（不启动服务器时可视化仍可用，仅同步功能不生效）。此时仍需要联网加载 CDN 上的 vis-network，否则图画不出来。

### `.topo` 文件的三个坑

写别的解析器时要注意（`topo-viewer.html` 和 MCP 的 `discover_topology` 都已处理）：

1. **编码**：文件头声明 `encoding="UNICODE"`，但实际写的是 **GBK**。Python 的 `ET.parse()` 会直接抛 `LookupError: unknown encoding: UNICODE`；浏览器不处理的话中文设备名/标注会变成乱码。
2. **标签名**：真实结构是 `devices/dev`、`lines/line/interfacePair`、`txttips/txttip`，不是 `<device>` / `<node>`。
3. **多段链路与接口编号**：一条 `<line>` 可以挂多个 `<interfacePair>`（多段链路），只取第一个会漏链路；接口索引是设备端口列表上的全局下标，交换机从 1 起、路由器等其他设备从 0 起。

## 工作原理

```
┌──────────────┐   stdio (MCP)   ┌──────────────────┐   TCP 127.0.0.1:2000+   ┌─────────────┐
│  AI 客户端    │ <─────────────> │ ensp_mcp_server  │ <─────────────────────> │ eNSP 设备    │
│ Claude / 其他 │                 │ 21 个工具         │                         │ 控制台端口    │
└──────────────┘                 └──────────────────┘                         └─────────────┘
                                        │
                                        ├── devices.json   （设备名 → 端口 / 凭据）
                                        └── topology.json  （Topo Viewer 落盘，供 AI 读取）
```

- MCP Server 通过标准输入输出与 AI 客户端通信，按需连接 eNSP 设备的本地控制台端口。
- 它**不启动设备、不修改 `.topo`**；设备必须在 eNSP GUI 里先启动。
- 所有设备操作最终都是标准 VRP 命令，回显原样返回。

## 已知限制

<details>
<summary><b>终端类设备（PC / Client / Server / Cloud / Hub）无法通过 MCP 配置</b></summary>

这是 eNSP 的机制限制，不是工具缺功能。这些设备在 `.topo` 里 `com_port="0"`，没有对外的控制台 TCP 端口，也没有对应的 VM 或进程；它们的全部状态只存在 `.topo` 的 `settings` 属性里。`execute_cli` / `get_running_config` 对它们一律无效。

需要 PC 侧地址时可走 DHCP：请用户在 GUI 里把 PC 改成「自动获取地址」一次，之后地址由交换机/路由器的 DHCP 地址池决定，而地址池是 MCP 能控的。不需要可变地址时，直接在 GUI 里手动设置。

</details>

<details>
<summary><b><code>health_check</code> 在设备启动期间可能误报 OK</b></summary>

设备还在启动时，`health_check` 可能同时报 `TCP 连接: OK` 和 `CLI 命令测试: OK`，但设备其实执行不了命令 —— 实测启动期间 `display version` 只回 `## `（eNSP 的启动进度输出）。

真正的判据是能否执行一条 `display` 命令。AR 路由器启动远慢于交换机（交换机约 1 分钟，AR 要数分钟），批量启动后别急着下配置。

</details>

<details>
<summary><b>eNSP 应用与拓扑生命周期工具的限制</b></summary>

- **`open_topology` 只在 eNSP 没运行时可靠。** eNSP 已在运行时，命令行把 `.topo` 转交给它的路基本走不通：已有非空拓扑时完全无效，一个画布都没有时超时无效。工具会如实报失败，并提示改用 GUI 的 Ctrl+O，或先 `close_ensp`。成功判据是交接前后画布状态有没有变化，不是画布上有没有设备标签。
- **`close_ensp` 只在没有打开拓扑时才干脆**（1–5 秒退出）。只要开着拓扑，关闭就会弹模态确认框把 `WM_CLOSE` 挡住，工具会报「未退出」并给出那个对话框的窗口句柄。工具不替你点按钮，也不 `taskkill`。
- **残留进程是 eNSP 自身行为**：关掉拓扑或 eNSP 后，`eNSP_Switch.exe` / `VBoxHeadless.exe` 可能残留且控制台端口消失（进程还在但 MCP 连不上）。工具只报告、不清理。
- 载入 `.topo` 只是画出拓扑，**设备不会自动启动**，需要在 GUI 里点「启动设备」（Ctrl+Alt+A）。eNSP 的 Ctrl+S 保存的是拓扑，**不是**设备配置。

</details>

<details>
<summary><b>回显解析与 ping 的历史坑（已修复）</b></summary>

- **分页回显**：`---- More ----` 处理不当会把相邻两行粘在一起。当前实现检测到分页时就地摘掉标记，兜底清理也不用会吃掉换行的 `\s`。
- **ANSI 转义**：设备回显可能夹带 ANSI 转义序列，必须先过滤再匹配提示符。
- **`[Y/N]` 被当成提示符**：宽松的提示符正则会把 `Are you sure to continue? [Y/N]` 当成命令结束，后续配置命令会被当成确认答案。现在统一用严格提示符匹配。
- **`ping -t` 单位**：VRP 的 `-t` 是**毫秒**。工具固定用 `-t 2000`；如果手工拼成 `-t 2`，每次 ping 都会在回应到达前放弃，表现为 100% 丢包，哪怕目标就是本机接口。
- **`save_config` 漏答文件名**：部分 S5700 / S3700 在 `[Y/N]` 之后还会追问 `Please input the file name ...`，不回车会导致配置没写盘却看起来成功。工具已补答默认文件名。

</details>

<details>
<summary><b>设备注册表会跨会话残留</b></summary>

`devices.json` 里的端口是本机 eNSP 动态分配的，设备重启或拓扑重开后会变化。陈旧条目会误导判断；设备重启后先 `auto_discover`，不要沿用旧注册表。

</details>

## 故障排查

| 症状 | 可能原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: No module named 'ensp_mcp_server'` | 工作目录不是仓库根目录 | 把客户端的 `cwd` 指到仓库根目录；Claude Code 直接用项目级 `.mcp.json` |
| 工具报设备未注册 / 连接失败 | 设备没启动，或 `devices.json` 端口已失效 | 先在 eNSP GUI 启动设备，再 `auto_discover` |
| `auto_discover` 找不到控制台端口 | eNSP 进程在，但设备没启动；或残留了僵尸进程 | 确认设备已启动；必要时重启 eNSP |
| `health_check` 报 OK，但命令执行不了 | 设备还在启动 | 等启动完成；以一条 `display` 命令是否成功为准 |
| 所有 ping 都不通，但 ARP / MAC / OSPF 邻居表正常 | 旧版本的 `ping -t` 单位错误 | 升级到当前版本；或改用表项判断 |
| `save_config` 显示成功，但重启后配置丢失 | 旧版本漏答文件名提示 | 升级；用 `display saved-configuration` 复核 |
| `open_topology` 报失败 | eNSP 已开着拓扑 | 在 GUI 里 Ctrl+O，或先 `close_ensp` 再打开 |
| `close_ensp` 报「未退出」 | 有拓扑打开，弹了模态确认框 | 到屏幕上手动点掉确认框 |
| 测试脚本在 Windows 控制台报 `UnicodeEncodeError` | 控制台是 GBK，脚本里有 `✗` 等字符 | 用 `PYTHONIOENCODING=utf-8 python tests/...` |
| 拓扑里的 PC / Server 改不了地址 | 终端类设备没有控制台端口 | 走 DHCP，或在 GUI 里手动设置 |

## 开发

### 项目结构

```
eNSP-AI/
├── README.md                 # 本文件
├── LICENSE                   # MIT
├── requirements.txt          # Python 依赖
├── .mcp.json                 # Claude Code 项目级 MCP 配置
├── tests/                    # 离线测试（不需要 eNSP 设备）
├── topo-viewer.html          # 拓扑可视化（浏览器）
├── topo-server.py            # 可视化本地服务器
└── ensp_mcp_server/          # MCP Server 主包
    ├── server.py              # MCP Server 入口 & 工具注册
    ├── config.py              # 设备注册表 & 自动发现
    ├── console.py             # 设备控制台连接（原生 socket）
    ├── exceptions.py          # 自定义异常
    └── tools/
        ├── cli.py             # CLI 执行 & 配置工具
        ├── ensp_app.py        # eNSP 应用与拓扑生命周期（Win32 ctypes）
        ├── inspection.py      # 巡检 & 实验验收
        └── topology.py        # .topo 解析 & 搜索
```

### 运行离线测试

两个测试都只用标准库，不需要 eNSP 设备，也不需要 pytest：

```bash
python tests/test_inspection_offline.py
python tests/test_ensp_app_offline.py
```

Windows 默认 GBK 控制台下如果报 `UnicodeEncodeError`，加上 UTF-8 输出：

```bash
PYTHONIOENCODING=utf-8 python tests/test_inspection_offline.py
```

### 实现约定

- **不要用 `telnetlib`**：Python 3.13 已移除。`console.py` 用原生 socket。
- **锁 `mcp < 2`**：见[安装](#安装)里的说明。
- **eNSP 生命周期工具不强制结束进程**：`close_ensp` 只发 `WM_CLOSE`，遇到模态框如实报告，不替用户点按钮。

## 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `ENSP_LOG_LEVEL` | 日志级别 | `WARNING` |
| `ENSP_EXE` | `eNSP_Client.exe` 完整路径，覆盖自动探测 | 自动探测 |

> 设备凭据按设备写在 `devices.json` 里（`register_device` 的 `username` / `password` 参数），没有全局用户名/密码环境变量。

## 来源与致谢

最初由 [Redmig110](https://github.com/Redmig110) 创建。

后续贡献包括：

- 控制台登录支持（USG6000V 等需要 `Username:` / `Password:` 的设备）；
- 分页回显粘连、ANSI 转义、`[Y/N]` 提示符误判、`ping -t` 单位、`save_config` 文件名提示等修复；
- `.topo` 解析的编码、多段链路、接口编号修复；
- 新增 `verify_expectations` / `inspect_topology` 验收与巡检工具；
- 新增 `open_ensp` / `close_ensp` / `open_topology` / `close_topology` 生命周期工具（Win32 ctypes，不依赖 pywin32）；
- 两个离线测试与 `mcp < 2` 依赖锁定。

## License

MIT，见 [LICENSE](LICENSE)。
