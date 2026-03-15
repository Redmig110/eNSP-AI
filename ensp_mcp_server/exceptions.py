"""
eNSP MCP Server 自定义异常
"""


class ENSPError(Exception):
    """基础异常"""
    pass


class DeviceNotFoundError(ENSPError):
    """设备未在注册表中找到"""
    def __init__(self, device: str):
        self.device = device
        super().__init__(f"设备 '{device}' 未注册，请先用 register_device 或 auto_discover 注册。")


class ConnectionError(ENSPError):
    """无法建立设备连接"""
    def __init__(self, device: str, detail: str = ""):
        self.device = device
        msg = f"无法连接设备 '{device}'"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


class CommandTimeoutError(ENSPError):
    """命令执行超时"""
    def __init__(self, device: str, command: str, timeout: int = 0):
        self.device = device
        self.command = command
        msg = f"设备 '{device}' 执行命令 '{command}' 超时"
        if timeout:
            msg += f"（{timeout}s）"
        super().__init__(msg)
