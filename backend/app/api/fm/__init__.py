"""文件管理端点（/api/fm）。

历史上是单文件 god-module，现按职责拆包：
- paths   纯路径工具（storage 前缀、父目录、扩展名、mime…）
- tree    FileObject 虚拟文件树（子项/子树查询、DirEntry 序列化、建目录、改父路径）
- crud    列目录/统计/删除/重命名/移动/复制/建目录文件/文本保存
- uploads 直传签凭证 + 登记 + 代理上传
- serve   sign / content / preview / download / search（取字节，多数 302 直连七牛）

对外仍暴露 `app.api.fm.router` 与若干 `_` 前缀原语（webdav.py 复用、tests 猴补丁），
故本 __init__ 汇总路由并重新导出这些名字，保持既有 import 不变。

文件夹用 FileObject(is_dir=True) 行表示；七牛 key 为不透明 uuid，
因此 rename/move 只改 path（不动七牛对象），copy 才真正复制七牛对象。
私有空间：preview/download 直接 302 到签名 URL，文本预览走 sign/content。
"""

from fastapi import APIRouter

from . import crud, serve, uploads

# tests 猴补丁的存储统计缓存（必须与 crud.stats 用的是同一个 dict 对象）
from .crud import _space_cache  # noqa: F401

# webdav.py 复用的文件树原语
from .paths import (  # noqa: F401
    _basename,
    _ext,
    _guess_mime,
    _parent_rel,
    _ts,
)
from .tree import (  # noqa: F401
    _children,
    _ensure_dirs,
    _get,
    _reparent,
    _subtree,
)

router = APIRouter()
# 列目录挂在空路径（历史线格式），直接注册到聚合 router（不走 include_router）
router.add_api_route("", crud.list_dir, methods=["GET"])
router.include_router(crud.router)
router.include_router(uploads.router)
router.include_router(serve.router)
