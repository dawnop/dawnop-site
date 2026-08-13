"""FastAPI 入口：应用创建、CORS、路由挂载、健康检查。"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    articles,
    auth,
    fm,
    monitor,
    pages,
    rollback,
    search,
    tags,
    viz,
    webdav,
)
from app.api import (
    settings as settings_api,
)
from app.config import settings
from app.core.bootstrap import ensure_builtin_pages
from app.core.db_identity import establish_database_identity
from app.core.errors import register_error_handlers
from app.core.process_guard import process_file_routes_disabled
from app.core.search import ensure_article_fts
from app.database import engine, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    if process_file_routes_disabled():
        # 受守护的回滚：不动库结构（建表/建 FTS 都是写操作，回滚不是做迁移的时机），
        # 只核定身份。身份在放行流量之前核定并闩死；抛出即拒绝启动，绝不带着未核实的
        # 库对外服务——起不来看得见，连错库看不见。
        establish_database_identity(settings.database_url)
    else:
        init_db()
        ensure_builtin_pages()
        ensure_article_fts(engine)
    yield


def require_file_routes_enabled() -> None:
    """哨兵在场时把 /api/fm 整个关掉（见 app/core/process_guard.py 的理由）。"""
    if process_file_routes_disabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="文件路由已在本进程停用",
        )


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# 统一异常 → {"detail": ...} JSON 响应
register_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(articles.router, prefix="/api/articles", tags=["articles"])
app.include_router(
    fm.router,
    prefix="/api/fm",
    tags=["files"],
    dependencies=[Depends(require_file_routes_enabled)],
)
app.include_router(monitor.router, prefix="/api/monitor", tags=["monitor"])
app.include_router(pages.router, prefix="/api/pages", tags=["pages"])
# 不在这里再写一遍 include_in_schema=False：两处写同一件事，任何一处被改掉，另一处都会
# 把它盖住，于是「探针不该出现在文档里」这条就没有任何一个变异体能推翻它。唯一的开关在
# app/api/rollback.py 的路由装饰器上。
app.include_router(rollback.router, prefix="/api/rollback")
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["settings"])
app.include_router(tags.router, prefix="/api/tags", tags=["tags"])
app.include_router(viz.router, prefix="/api/viz", tags=["viz"])
app.include_router(webdav.router, prefix="/dav", tags=["webdav"])
