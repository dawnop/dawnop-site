"""ORM 模型集中导出，供 Base.metadata.create_all 注册。"""

from app.models.article import Article
from app.models.file_object import FileObject
from app.models.page import Page
from app.models.pending_upload import PendingUpload
from app.models.setting import Setting
from app.models.tag import Tag
from app.models.user import User
from app.models.viz import VizComponent

__all__ = [
    "User",
    "Article",
    "FileObject",
    "Page",
    "PendingUpload",
    "Setting",
    "Tag",
    "VizComponent",
]
