"""标签相关的响应模型。"""
from pydantic import BaseModel, ConfigDict


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str


class TagWithCount(TagOut):
    """标签 + 已发布文章数（用于标签云/后台）。"""

    count: int
