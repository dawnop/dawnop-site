"""文件管理写端点的请求体模型。

历史上这些端点用 `payload: dict = Body(...)`（无类型、无 /docs、无入参校验）。
这里补上 Pydantic 模型：字段全部可选（沿用原 `.get()` 的宽松语义，缺字段仍由
处理函数内的手动校验给出 400），`extra="ignore"` 容忍历史客户端多带的字段。
处理函数内部仍以 dict 形态消费（`model.model_dump()`），逻辑保持不变。
"""
from pydantic import BaseModel, ConfigDict


class _FmBody(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PathNameIn(_FmBody):
    """create-folder / create-file / upload-token：目标目录 + 名称。"""
    path: str | None = None
    name: str | None = None


class RenameIn(_FmBody):
    path: str | None = None
    item: str | None = None
    name: str | None = None


class DeleteItem(_FmBody):
    path: str | None = None
    type: str | None = None


class DeleteIn(_FmBody):
    path: str | None = None
    items: list[DeleteItem] | None = None


class MoveCopyIn(_FmBody):
    path: str | None = None
    destination: str | None = None
    sources: list[str] | None = None


class SaveIn(_FmBody):
    path: str | None = None
    content: str | None = None


class RegisterIn(_FmBody):
    path: str | None = None
    key: str | None = None
    size: int | None = None
    content_type: str | None = None
