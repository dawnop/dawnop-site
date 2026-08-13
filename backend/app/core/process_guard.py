"""文件路由的进程级闩存：哨兵文件只读一次，结论此后不变。

回滚到 FastAPI 是「把流量切到一份冻结了的实现上」。冻结的那一份仍然带着 `/api/fm`
——七牛对象存储的读写面。回滚是应急动作，谁也不希望在那种时刻还有一条会往对象存储里
写东西的路径处于可用状态，尤其是它和 Dawn 侧共用同一个桶。

哨兵文件 `/etc/dawnop/fastapi-file-routes-disabled` 存在 = 这次是「受守护的回滚」：
文件路由关掉，启动期只核定库身份、不动库结构（见 app/main.py 的 lifespan）。

**为什么要闩**：哨兵是磁盘上的文件，每请求去 stat 一次意味着有人 rm 掉它，正在服务的
进程就会中途改变行为——一半请求 503、一半放行，而日志上看不出分界。判定只做一次、
之后只读内存，进程的行为在它的整个生命周期里就是一个常量。要改结论只能重启，而重启是
一个会被记录、会被观察的动作。
"""

import os

SENTINEL_PATH = "/etc/dawnop/fastapi-file-routes-disabled"


class ProcessFileRouteGuard:
    """一旦判定就不再重读磁盘。"""

    def __init__(self, sentinel_path: str = SENTINEL_PATH) -> None:
        self._sentinel_path = sentinel_path
        self._disabled: bool | None = None

    @property
    def sentinel_path(self) -> str:
        return self._sentinel_path

    @property
    def latched(self) -> bool:
        return self._disabled is not None

    def file_routes_disabled(self) -> bool:
        if self._disabled is None:
            self._disabled = os.path.exists(self._sentinel_path)
        return self._disabled

    def reset(self) -> None:
        """仅供测试：打回未判定状态。生产路径没有任何调用点。"""
        self._disabled = None


_guard = ProcessFileRouteGuard()


def process_file_routes_disabled() -> bool:
    return _guard.file_routes_disabled()


def process_guard_latched() -> bool:
    return _guard.latched


def reset_process_guard(sentinel_path: str = SENTINEL_PATH) -> None:
    """仅供测试：换哨兵路径并打回未判定状态。"""
    _guard._sentinel_path = sentinel_path  # noqa: SLF001
    _guard.reset()
