"""标签相关的请求/响应模型。"""
from pydantic import BaseModel, ConfigDict


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str


class TagWithCount(TagOut):
    """标签 + 文章数（公开列表为已发布数，后台列表含草稿）。"""

    count: int


class TagUpdate(BaseModel):
    """重命名标签。slug 由后端按新名重新生成。"""

    name: str


class TagMerge(BaseModel):
    """合并标签：source 的文章关联并入 target，随后删除 source。"""

    source_id: int
    target_id: int
