"""文件相关的请求/响应模型。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FileObjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    filename: str
    content_type: str
    size: int
    created_at: datetime


class FileListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: list[FileObjectOut]


class UploadTokenRequest(BaseModel):
    filename: str


class UploadTokenResponse(BaseModel):
    token: str
    key: str
    expires: int


class RegisterFileRequest(BaseModel):
    """前端直传七牛成功后，回调后端登记元信息。"""

    key: str
    filename: str
    content_type: str | None = None
    size: int | None = None


class FileUrlResponse(BaseModel):
    url: str
    expires: int
