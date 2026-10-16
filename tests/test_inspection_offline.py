# -*- coding: utf-8 -*-
"""
inspection / cli / console 的离线测试 —— 不需要 eNSP 设备。

跑法（任意工作目录）：
    python tests/test_inspection_offline.py

只用标准库，不依赖 pytest。退出码 0 = 全通过，1 = 有失败。

覆盖：
  1. evaluate_check          —— 纯判定逻辑（含词边界陷阱、坏正则）
  2. 巡检用的解析辅助函数      —— 日志过滤 / 配置归一化 / 接口正则 / 截断
  3. _load_checks            —— 清单加载与校验（BOM、裸数组、字段缺失）
  4. verify_expectations     —— 错误路径（清单坏、设备没起、名字没注册）
  5. inspect_topology        —— 不可达设备与统计行
  6. _cli_rejected           —— `^down: standby` 图例误判那个修复
  7. 分页回显                —— 假设备回放 >1 屏输出，验两个修复判据
  8. 注册表身份              —— auto_discover 之后各模块不能拿到旧 dict

第 7 节的假设备是本文件的核心：`---- More ----` 那个 bug 只在超过一屏的
回显上才出现，没有设备就只能在字节层面回放。末尾有一组对照，用**修复前**的
正则处理同一份回显，确认它确实会粘连 —— 否则这组测试等于没测。
"""
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from ensp_mcp_server.tools import inspection as I
from ensp_mcp_server.tools import cli as C
from ensp_mcp_server.tools.cli import _cli_rejected
from ensp_mcp_server.console import HuaweiConsole
import ensp_mcp_server.config as cfg

TMP_DIR = tempfile.mkdtemp(prefix="ensp_offline_test_")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  ok   " if cond else "  FAIL ") + name
          + (("   " + detail) if detail and not cond else ""))


def eq(name, got, want):
    check(name, got == want, f"got={got!r}  want={want!r}")


def section(t):
    print("\n=== " + t + " ===")


# ====================================================================
# 1. evaluate_check —— 纯判定逻辑
# ====================================================================
section("1. evaluate_check 纯判定")

eq("一条预期都没写 -> INFO",
   I.evaluate_check({"device": "x", "command": "y"}, "whatever")["status"], I.INFO)

eq("空 expect_contains 列表也算没写预期 -> INFO",
   I.evaluate_check({"expect_contains": []}, "whatever")["status"], I.INFO)

eq("contains 命中 -> PASS",
   I.evaluate_check({"expect_contains": ["Master"]}, "State : Master")["status"], I.PASS)

r = I.evaluate_check({"expect_contains": ["Master"]}, "State : Backup")
eq("contains 未命中 -> FAIL", r["status"], I.FAIL)
eq("contains 未命中的 problems 文案", r["problems"], ["缺少: Master"])

r = I.evaluate_check({"expect_not_contains": ["Backup"]}, "State : Backup")
eq("not_contains 命中 -> FAIL", r["status"], I.FAIL)
eq("not_contains 的 problems 文案", r["problems"], ["不应出现: Backup"])

eq("not_contains 未出现 -> PASS",
   I.evaluate_check({"expect_not_contains": ["Backup"]}, "State : Master")["status"], I.PASS)

eq("expect_match 命中 -> PASS",
   I.evaluate_check({"expect_match": r"PriorityConfig\s*:\s*120"},
                    "PriorityConfig      : 120")["status"], I.PASS)

r = I.evaluate_check({"expect_match": r"PriorityConfig\s*:\s*120"},
                     "PriorityConfig      : 100")
eq("expect_match 未命中 -> FAIL", r["status"], I.FAIL)
eq("expect_match 文案", r["problems"], ["未匹配正则: PriorityConfig\\s*:\\s*120"])

r = I.evaluate_check({"expect_match": "([unclosed"}, "x")
eq("正则写错 -> FAIL 而不是抛异常", r["status"], I.FAIL)
check("正则写错会说明原因", "正则写法有误" in r["problems"][0], str(r["problems"]))

r = I.evaluate_check(
    {"expect_contains": ["A"], "expect_not_contains": ["B"], "expect_match": "C"},
    "only B here")
eq("多个问题会一起报", len(r["problems"]), 3)
eq("同时有问题时状态是 FAIL", r["status"], I.FAIL)

# CLAUDE.md 里点名的词边界陷阱
check("\\b120\\b 不会命中 1200（这是正确用法）",
      I.evaluate_check({"expect_match": r"\b120\b"}, "priority 1200")["status"] == I.FAIL)
check("裸 120 会静默命中 1200（CLAUDE.md 记的坑，工具行为符合记载）",
      I.evaluate_check({"expect_match": "120"}, "priority 1200")["status"] == I.PASS)
check("contains 是子串匹配、不区分词边界",
      I.evaluate_check({"expect_contains": ["120"]}, "priority 1200")["status"] == I.PASS)


# ====================================================================
# 2. 巡检用的解析辅助函数
# ====================================================================
section("2. 巡检用的解析辅助函数")

check("级别 2 的日志 -> 值得看",
      I._log_is_interesting("Oct 16 2026 10:00:00 AR1 %%01OSPF/2/OSPF_NOT_ENABLED(l)[1]: ..."))
check("级别 4 的普通消息 -> 不看",
      not I._log_is_interesting("Oct 16 2026 10:00:00 AR1 %%01ENTITYTRAP/4/ENTITYTRAP(l)[2]: ..."))
check("级别 4 但带 Failed -> 看",
      I._log_is_interesting("Oct 16 2026 10:00:00 AR1 %%01SHELL/4/Telnet_loginFailed(l)[3]: ..."))
check("带 Error -> 看", I._log_is_interesting("something Error happened"))
check("带 DUPLICATE -> 看", I._log_is_interesting("VLAN DUPLICATE found"))
check("LINK_STATE 噪声 -> 过滤", not I._log_is_interesting("AR1 %%01IFNET/3/LINK_STATE(l)[0]: ..."))
check("IF_STATE 噪声 -> 过滤", not I._log_is_interesting("AR1 %%01IFNET/3/IF_STATE(l)[0]: ..."))
check("register success（成功消息,级别 4）-> 过滤",
      not I._log_is_interesting("DEV/4/ENTUP ... register success"))

norm = I._normalize_config("sysname R1\n#\n!\nvlan batch 10\nreturn\n\n   indented  \n")
eq("_normalize_config 丢掉 # / ! / return / 空行",
   norm, {"sysname R1", "vlan batch 10", "indented"})
eq("_normalize_config 两侧空白已去", ("  indented  " in norm), False)

eq("_clip 短文本原样", I._clip("abc"), "abc")
clipped = I._clip("x" * 2500)
check("_clip 超长会截断", len(clipped) < 2500)
check("_clip 截断会说明原文长度", "原文共 2500 字符" in clipped)

brief = """Interface                         IP Address      Physical     Protocol
Vlanif10                          192.168.10.1    up           up
Vlanif20                          192.168.20.1    down         down
GigabitEthernet0/0/1              10.0.0.1        up           down
                                 ^down: standby
"""
parsed = I._IP_INTF_RE.findall(brief)
eq("接口正则抓到 3 行数据（图例行不算）", len(parsed), 3)
eq("其中 2 个是 down",
   len([p for p in parsed if "down" in p[2].lower() or "down" in p[3].lower()]), 2)
check("图例行的 IP 不会被当成接口地址",
      not any("standby" in p[0] for p in parsed), str(parsed))


# ====================================================================
# 3. _load_checks —— 清单加载与校验
# ====================================================================
section("3. 清单加载 _load_checks")

ok_file = os.path.join(TMP_DIR, "checks_ok.json")
with open(ok_file, "w", encoding="utf-8") as f:
    json.dump({"checks": [{"device": "LSW1", "command": "display vlan"}]},
              f, ensure_ascii=False)
eq('能从 {"checks": [...]} 读出来', len(I._load_checks(None, ok_file)), 1)

arr_file = os.path.join(TMP_DIR, "checks_arr.json")
with open(arr_file, "w", encoding="utf-8") as f:
    json.dump([{"device": "LSW1", "command": "display vlan"}], f, ensure_ascii=False)
eq("裸数组也接受", len(I._load_checks(None, arr_file)), 1)

bom_file = os.path.join(TMP_DIR, "checks_bom.json")
with open(bom_file, "w", encoding="utf-8-sig") as f:
    json.dump([{"device": "LSW1", "command": "display vlan"}], f, ensure_ascii=False)
check("UTF-8 BOM（记事本存的）能读", len(I._load_checks(None, bom_file)) == 1)

no_checks = os.path.join(TMP_DIR, "checks_no_key.json")
with open(no_checks, "w", encoding="utf-8") as f:
    json.dump({"items": []}, f)
try:
    I._load_checks(None, no_checks)
    check("对象缺 checks 字段应当报错", False)
except ValueError as e:
    check("对象缺 checks 字段会报错", "checks" in str(e), str(e))

merged = I._load_checks([{"device": "B", "command": "c2"}], ok_file)
eq("文件 + 内联会合并", len(merged), 2)
eq("文件里的在前", merged[0]["device"], "LSW1")
eq("内联的在后", merged[1]["device"], "B")

for bad, why in [
    ([{"command": "display vlan"}], "缺 device"),
    ([{"device": "LSW1"}], "缺 command"),
    (["not-a-dict"], "不是对象"),
    ("not-a-list", "checks 不是数组"),
]:
    try:
        I._load_checks(bad, None)
        check(f"非法清单应当报错（{why}）", False)
    except ValueError:
        check(f"非法清单会报错（{why}）", True)


# ====================================================================
# 4. verify_expectations —— 错误路径（不需要设备）
# ====================================================================
section("4. verify_expectations 的错误路径")

out = I.verify_expectations(checks=[])
check("空清单会提示", "没有要执行的检查" in out, out)

out = I.verify_expectations(checklist_file=os.path.join(TMP_DIR, "not_exist.json"))
check("清单文件不存在 -> 清单加载失败", "清单加载失败" in out, out)

# 注册了但连不上（设备没起 / 端口被拒）
out = I.verify_expectations(checks=[{"device": "GhostRegistered",
                                     "command": "display vlan",
                                     "expect_contains": ["VLAN ID"]}])
check("连不上时仍会把两条都跑完", out.count("[ERROR]") >= 1, out)

# 没注册的名字 -> _connect_error 应当给出对排错有用的提示
out = I.verify_expectations(checks=[{"device": "NoSuchDeviceXYZ", "command": "display vlan"}])
check("没注册的设备 -> ERROR", "[ERROR]" in out, out)
check("提示「不在注册表里」", "不在注册表里" in out, out)
check("不会只丢一句 getaddrinfo failed", "getaddrinfo" not in out, out)

# 一条挂了不影响后面
out = I.verify_expectations(checks=[
    {"device": "NoSuchDeviceXYZ", "command": "display vlan"},
    {"device": "AlsoMissingXYZ", "command": "display vlan"},
])
eq("两条都执行了（一条挂了不影响后面）", out.count("[ERROR]"), 2)

out = I.verify_expectations(checks=[{"device": "NoSuchDeviceXYZ", "command": "display vlan",
                                     "desc": "看看 desc 显示不显示"}])
check("desc 会当作标签显示", "看看 desc 显示不显示" in out, out)
check("没写预期时标签回退成命令本身",
      "display vlan" in I.verify_expectations(
          checks=[{"device": "NoSuchDeviceXYZ", "command": "display vlan"}]), out)


# ====================================================================
# 5. inspect_topology —— 不可达与统计
# ====================================================================
section("5. inspect_topology")

saved_I = dict(I._device_registry)      # 存内容 —— 下面都是原地改，存引用会被一起改掉
try:
    # 不能写死 2000 —— 真跑 eNSP 时那个端口是活的，测试会随环境变成红/绿。
    # 先绑一个临时端口再放掉，保证这个端口确实没人监听。
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()

    cfg._replace_registry({})
    out = I.inspect_topology()
    check("一台都没注册时给出提示", "没有注册任何设备" in out, out)

    cfg._replace_registry({"LSW1": dead_port})   # 注册了但没人监听
    out = I.inspect_topology()
    check("注册但连不上 -> 不可达（不会误报 OK）", "✗ 不可达" in out, out)
    check("报错原因是连接失败", "连接或执行失败" in out, out)
    check("统计行写了 0 可达", "0 可达" in out and "1 不可达" in out, out)

    out = I.inspect_topology(["GhostDeviceXYZ"])
    check("名字没注册时报 ✗ 不可达", "✗ 不可达" in out, out)
    check("提示「不在注册表里」", "不在注册表里" in out, out)
    check("会把已注册的设备名列出来", "LSW1" in out, out)
finally:
    cfg._replace_registry(saved_I)


# ====================================================================
# 6. _cli_rejected —— 图例误判那个修复
# ====================================================================
section("6. _cli_rejected 图例误判")

legend_out = """Interface                         IP Address      Physical     Protocol
Vlanif100                         66.0.0.1        up           up
GigabitEthernet0/0/1              10.0.0.1        up           up
                                 ^down: standby
"""
check("display ip interface brief 的图例行 ^down: standby 不算被拒",
      not _cli_rejected(legend_out))

for real, why in [
    ("Error: Unrecognized command found at '^' position.", "Unrecognized command"),
    ("Error: Wrong parameter found at '^' position.", "Wrong parameter"),
    ("Error: Incomplete command found at '^' position.", "Incomplete command"),
    ("Error: Ambiguous command found at '^' position.", "Ambiguous command"),
    ("Error: Too many parameters found at '^' position.", "Too many parameters"),
]:
    check(f"真实报错仍能识别（{why}）", _cli_rejected(real), real)

check("单独占一行的 ^ 算被拒（VRP 报错指示符）",
      _cli_rejected("  vrrp vrid 1 virtual-ip\n                          ^\n"))
check("行内 ^ 不算被拒（只有独占一行才算）",
      not _cli_rejected("  some output ^ more text\n"))
check("完全正常的输出不算被拒",
      not _cli_rejected("<R1>display vlan\nTotal 3 VLANs exist\n"))


# ====================================================================
# 7. 分页回显 —— 假设备回放 >1 屏的输出
# ====================================================================
section("7. 分页回显（假设备回放）")


class FakeDevice(threading.Thread):
    """按页回放固定回显，页间插 ---- More ---- 并要求收到空格才继续。"""

    def __init__(self, pages):
        super().__init__(daemon=True)
        self.pages = pages
        self.raw_sent = b""
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]

    def _send(self, conn, text):
        self.raw_sent += text.encode()
        conn.sendall(text.encode())

    def run(self):
        conn, _ = self.sock.accept()
        try:
            self._send(conn, "<R1>\r\n")
            buf, state, page_i = b"", "idle", 0
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                if state == "idle":
                    buf += data
                    while b"\r\n" in buf:
                        line, buf = buf.split(b"\r\n", 1)
                        if not line.strip():
                            continue
                        self._send(conn, line.decode() + "\r\n")   # 回显命令
                        self._send_page(conn, 0)
                        if len(self.pages) == 1:
                            state = "idle"
                        else:
                            page_i, state = 1, "await"
                        break
                elif b" " in data:                                  # 等空格
                    self._send_page(conn, page_i)
                    page_i += 1
                    if page_i >= len(self.pages):
                        state = "idle"
        except OSError:
            pass
        finally:
            conn.close()

    def _send_page(self, conn, i):
        last = (i == len(self.pages) - 1)
        out = "\r\n".join(self.pages[i])
        out += "\r\n<R1>" if last else "\r\n---- More ----\r\n"
        self._send(conn, out)


# 造一份 >1 屏的配置回显，页面边界正好落在 `#` 分隔行上
lines = ["sysname LSW1", "#"]
for v in (10, 20, 100):
    lines += [f"vlan batch {v}", "#"]
    lines += [f"interface Vlanif{v}", f" ip address 192.168.{v}.1 255.255.255.0", "#"]
for p in range(1, 7):
    lines += [f"interface GigabitEthernet0/0/{p}",
              " port link-type access",
              " port default vlan 10",
              "#"]
lines += ["return"]
hash_idx = [i for i, l in enumerate(lines) if l == "#"]
split1, split2 = hash_idx[4], hash_idx[8]
pages = [lines[:split1], lines[split1:split2], lines[split2:]]
print(f"  假设备: 共 {len(lines)} 行, 分 {len(pages)} 页, "
      f"边界落在第 {split1} / {split2} 行（都是 # 分隔行）")

dev = FakeDevice(pages)
dev.start()
time.sleep(0.2)

console = HuaweiConsole("127.0.0.1", dev.port, timeout=8)
console.connect()
outs = [console.send_command("display current-configuration", timeout=8) for _ in range(6)]
console.disconnect()

counts = [len(o.splitlines()) for o in outs]
print(f"  连打 6 次的行数: {counts}")
eq("连打 6 次行数完全一致", len(set(counts)), 1)

all_lines = [ln for o in outs for ln in o.splitlines()]
check("回显里没有 More 残留", not any("More" in ln for ln in all_lines))
check("没有非行首位置的 #（相邻行粘连的判据）",
      not [ln for ln in all_lines if "#" in ln and ln.strip() != "#"],
      str([ln for ln in all_lines if "#" in ln and ln.strip() != "#"][:3]))
check("没有把两行粘成一行（每行都能在原内容里找到）",
      all(ln.strip() == "" or any(ln.strip() == l.strip() for l in lines)
          for ln in all_lines),
      str([ln for ln in all_lines if ln.strip()
           and not any(ln.strip() == l.strip() for l in lines)][:3]))
check("内容没丢：关键配置行都在",
      all(any(l.strip() == ln.strip() for ln in all_lines)
          for l in lines if l.strip()),
      "缺失: " + str([l for l in lines if l.strip()
                      and not any(l.strip() == ln.strip() for ln in all_lines)][:3]))

print(f"  6 次回显共 {len(all_lines)} 行，其中空行 "
      f"{sum(1 for ln in all_lines if ln.strip() == '')} 行")

# 对照：把同一份原始字节流用「修复前」的兜底正则处理一遍。
# 没有这组对照，上面几条断言在 bug 复发时也可能因为假设备回放方式不对而仍然全绿。
raw = dev.raw_sent.decode()
old_cleaned = re.sub(r"\s*---- More ----\s*", "", raw)
old_lines = old_cleaned.replace("\r\n", "\n").replace("\r", "").splitlines()
old_glued = [ln for ln in old_lines if "#" in ln and ln.strip() != "#"]
print(f"  [对照] 用修复前的 \\s*---- More ----\\s* 清理同一份回显，"
      f"粘连行 {len(old_glued)} 处: {old_glued[:2]}")
check("对照实验证明这组测试能抓到修复前的 bug", len(old_glued) > 0,
      "旧正则也没粘连 —— 假设备的回放方式复现不出该 bug")


# ====================================================================
# 8. 注册表身份 —— auto_discover 之后不能拿到旧 dict
# ====================================================================
section("8. 注册表身份（auto_discover 之后的失效回归）")

check("导入时 inspection / cli / config 指向同一个 dict",
      I._device_registry is cfg._device_registry is C._device_registry)

saved = dict(I._device_registry)        # 同样存内容
try:
    # 走真实路径：auto_discover_devices() 与 _load_registry() 都经由它
    cfg._replace_registry({"R1": 2043, "LSW1": 2000})

    check("替换后 inspection 仍指向同一个 dict", I._device_registry is cfg._device_registry)
    check("替换后 cli 仍指向同一个 dict", C._device_registry is cfg._device_registry)
    check("inspection 能看到 auto_discover 发现的新设备 R1",
          I._device_registry.get("R1") == 2043,
          f"inspection 看到的是 {list(I._device_registry)}")
    check("_looks_registered 认可刚发现的 R1（否则会把新设备误报成未注册）",
          I._looks_registered("R1"))
    check("inspect_topology() 巡检的是 discover 之后的设备",
          set(I._device_registry.keys()) == {"R1", "LSW1"},
          f"实际是 {list(I._device_registry.keys())}")
    check("multi_health_check() 的默认设备列表同样是新的",
          set(C._device_registry.keys()) == {"R1", "LSW1"},
          f"cli 看到的是 {list(C._device_registry)}")
finally:
    cfg._replace_registry(saved)

# 源码级兜底：config.py 里不许再出现对 _device_registry 的重新绑定
src = open(os.path.join(REPO, "ensp_mcp_server", "config.py"), encoding="utf-8").read()
rebinds = [ln.strip() for ln in src.splitlines()
           if re.match(r"\s*_device_registry\s*=", ln)]
check("config.py 里没有 _device_registry 的重新绑定语句",
      not rebinds, f"发现: {rebinds}")


# ====================================================================
shutil.rmtree(TMP_DIR, ignore_errors=True)
print("\n" + "=" * 60)
failed = [(n, d) for n, ok, d in RESULTS if not ok]
print(f"共 {len(RESULTS)} 项断言：{len(RESULTS) - len(failed)} 通过, {len(failed)} 失败")
for n, d in failed:
    print(f"  FAIL  {n}\n        {d}")
sys.exit(1 if failed else 0)
