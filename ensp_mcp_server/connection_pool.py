"""
eNSP MCP Server 连接池

按 (host, port) 缓存 netmiko ConnectHandler，空闲超时自动断开。
"""
import functools
import logging
import threading
import time
from typing import Optional
from netmiko import ConnectHandler

from .config import get_device_info
from .exceptions import ConnectionError

logger = logging.getLogger("ensp_mcp.pool")

# 空闲超时（秒）
IDLE_TIMEOUT = 120


class _PoolEntry:
    """连接池条目"""
    __slots__ = ("connection", "last_used", "lock")

    def __init__(self, connection):
        self.connection = connection
        self.last_used = time.monotonic()
        self.lock = threading.Lock()


class ConnectionPool:
    """
    netmiko 连接池

    - 按 (host, port) 缓存连接
    - 空闲超时自动断开
    - is_alive() 检测 + 失败重试
    """

    def __init__(self, idle_timeout: int = IDLE_TIMEOUT):
        self._pool: dict[tuple[str, int], _PoolEntry] = {}
        self._lock = threading.Lock()
        self._idle_timeout = idle_timeout
        # 启动清理线程
        self._cleaner = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleaner.start()

    def get_connection(
        self,
        device_ip: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> ConnectHandler:
        """
        获取设备连接（复用或新建）

        Args:
            device_ip: 设备名称或 IP
            username: 可选用户名覆盖
            password: 可选密码覆盖

        Returns:
            netmiko ConnectHandler
        """
        device_info = get_device_info(device_ip, username, password)
        key = (device_info["host"], device_info["port"])

        with self._lock:
            entry = self._pool.get(key)

        if entry is not None:
            with entry.lock:
                try:
                    if entry.connection.is_alive():
                        entry.last_used = time.monotonic()
                        logger.debug("复用连接: %s:%d", *key)
                        return entry.connection
                except Exception:
                    pass
                # 连接已失效，移除
                logger.debug("连接已失效，重新创建: %s:%d", *key)
                self._disconnect_entry(entry)
                with self._lock:
                    self._pool.pop(key, None)

        # 新建连接
        try:
            conn = ConnectHandler(**device_info)
        except Exception as e:
            raise ConnectionError(device_ip, str(e))

        new_entry = _PoolEntry(conn)
        with self._lock:
            self._pool[key] = new_entry

        logger.debug("新建连接: %s:%d", *key)
        return conn

    def release(self, device_ip: str):
        """显式释放连接（断开并从池中移除）"""
        device_info = get_device_info(device_ip)
        key = (device_info["host"], device_info["port"])
        with self._lock:
            entry = self._pool.pop(key, None)
        if entry:
            self._disconnect_entry(entry)

    def close_all(self):
        """关闭所有连接"""
        with self._lock:
            entries = list(self._pool.values())
            self._pool.clear()
        for entry in entries:
            self._disconnect_entry(entry)
        logger.info("所有连接已关闭")

    def _disconnect_entry(self, entry: _PoolEntry):
        """安全断开一个连接"""
        try:
            entry.connection.disconnect()
        except Exception:
            pass

    def _cleanup_loop(self):
        """后台清理空闲连接"""
        while True:
            time.sleep(30)
            now = time.monotonic()
            expired_keys = []

            with self._lock:
                for key, entry in self._pool.items():
                    if now - entry.last_used > self._idle_timeout:
                        expired_keys.append(key)

            for key in expired_keys:
                with self._lock:
                    entry = self._pool.pop(key, None)
                if entry:
                    logger.debug("空闲超时，断开连接: %s:%d", *key)
                    self._disconnect_entry(entry)


# 全局连接池实例
pool = ConnectionPool()


def with_connection(func):
    """
    装饰器：自动从连接池获取连接，传入 net_connect 参数。
    如果连接失效则重试一次。

    被装饰函数签名:
        func(device_ip, ..., net_connect=None, ...) -> str
    """
    @functools.wraps(func)
    def wrapper(device_ip: str, *args, **kwargs):
        for attempt in range(2):
            try:
                conn = pool.get_connection(
                    device_ip,
                    username=kwargs.get("username"),
                    password=kwargs.get("password"),
                )
                kwargs["net_connect"] = conn
                return func(device_ip, *args, **kwargs)
            except Exception as e:
                if attempt == 0:
                    # 第一次失败，释放连接重试
                    logger.warning("连接失败，重试: %s - %s", device_ip, e)
                    pool.release(device_ip)
                    continue
                raise
        # 不应到达这里
        return func(device_ip, *args, **kwargs)
    return wrapper
