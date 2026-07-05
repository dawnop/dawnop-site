"""通用单值 TTL 缓存：替代各处手写的 {"at":..,"val":..} + 时间戳判断。

用法：
    _cache = TTLCache(300)
    def compute(): ...            # 真正取数（可能打第三方 API）
    val = _cache.get(compute)     # 命中且未过期直接返回，否则调 compute 重算
    _cache.get(compute, force=True)  # 跳过缓存强制重算
    _cache.invalidate()           # 清空（下次必重算；测试里常用）

失败值（如 None）也会被缓存（用 _UNSET 哨兵区分「未取过」与「取到 None」），
故第三方失败时不会每次请求都重打——与原手写缓存语义一致。
"""
import time

_UNSET = object()


class TTLCache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self.at = 0.0  # 上次成功取数的时间戳（供 cached_at 展示）
        self._val = _UNSET

    def get(self, loader, *, force: bool = False):
        now = time.time()
        if not force and self._val is not _UNSET and now - self.at < self.ttl:
            return self._val
        self._val = loader()
        self.at = now
        return self._val

    def invalidate(self) -> None:
        self._val = _UNSET
        self.at = 0.0
