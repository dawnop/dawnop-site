"""统一错误处理：把各类异常收敛成 {"detail": <可读消息>} 的 JSON 响应。

这样前端 axios 拦截器只需统一读取 `error.response.data.detail` 即可提示，
不必为不同错误形状写分支。HTTPException 已是 {detail}，这里主要规整校验错误
（默认是数组）与未捕获异常（默认会泄漏栈/500 HTML）。
"""
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# 422 字面量：避免不同 starlette 版本对 HTTP_422 常量命名的弃用告警
_HTTP_422 = 422

_SKIP_LOC = {"body", "query", "path", "header", "cookie"}


def _first_validation_message(exc: RequestValidationError) -> str:
    """取首条校验错误拼成一句中文提示，如「title：字段不能为空」。"""
    errors = exc.errors()
    if not errors:
        return "请求参数有误"
    err = errors[0]
    loc = err.get("loc", ())
    field = next((str(p) for p in reversed(loc) if p not in _SKIP_LOC), "")
    msg = err.get("msg", "参数有误")
    return f"{field}：{msg}" if field else msg


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _on_validation(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=_HTTP_422,
            content={"detail": _first_validation_message(exc)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _on_http(request: Request, exc: StarletteHTTPException):
        # 保留状态码与可能的响应头（如 401 的 WWW-Authenticate）
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _on_unhandled(request: Request, exc: Exception):
        # 生产环境不向客户端泄漏堆栈
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "服务器内部错误"},
        )
