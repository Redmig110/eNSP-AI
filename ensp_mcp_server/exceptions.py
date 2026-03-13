"""
eNSP MCP Server 自定义异常体系

提供细粒度的异常分类，便于工具函数精确处理错误。
"""


class ENSPError(Exception):
    """eNSP MCP Server 基础异常"""
    pass


class DeviceNotFoundError(ENSPError):
    """设备未在注册表中找到"""
    def __init__(self, device: str):
        self.device = device
        super().__init__(f"设备 '{device}' 未注册，请先用 register_device 或 auto_discover 注册。")


class ConnectionError(ENSPError):
    """无法建立设备连接（TCP 超时、端口未监听等）"""
    def __init__(self, device: str, detail: str = ""):
        self.device = device
        msg = f"无法连接设备 '{device}'"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


class AuthenticationError(ENSPError):
    """认证失败（用户名/密码错误）"""
    def __init__(self, device: str):
        self.device = device
        super().__init__(f"设备 '{device}' 认证失败，请检查用户名和密码。")


class CommandError(ENSPError):
    """命令执行失败（设备返回错误）"""
    def __init__(self, device: str, command: str, detail: str = ""):
        self.device = device
        self.command = command
        msg = f"设备 '{device}' 执行命令 '{command}' 失败"
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
