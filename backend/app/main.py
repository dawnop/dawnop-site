"""FastAPI 入口：应用创建、CORS、路由挂载、健康检查。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import articles, auth, fm, pages, tags, viz
from app.config import settings
from app.core.errors import register_error_handlers
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


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
app.include_router(fm.router, prefix="/api/fm", tags=["files"])
app.include_router(pages.router, prefix="/api/pages", tags=["pages"])
app.include_router(tags.router, prefix="/api/tags", tags=["tags"])
app.include_router(viz.router, prefix="/api/viz", tags=["viz"])
