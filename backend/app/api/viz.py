"""动态可视化组件接口：公开读取（按 slug）+ 受保护的 CRUD。

路由顺序：列表 GET "" 与公开 GET /{slug} 不冲突（空路径 vs 占位）；
写操作用整型 id，与字符串 slug 路由不同方法/不同模板，互不遮挡。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.crud import get_or_404
from app.deps import get_current_user, get_db
from app.models.user import User
from app.models.viz import VizComponent
from app.schemas.viz import VizCreate, VizOut, VizPublic, VizUpdate

router = APIRouter()


def _ensure_slug_free(db: Session, slug: str, exclude_id: int | None = None) -> None:
    q = db.query(VizComponent).filter(VizComponent.slug == slug)
    if exclude_id is not None:
        q = q.filter(VizComponent.id != exclude_id)
    if q.first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"标识 {slug} 已存在")


# ---------- 受保护：管理 ----------


@router.get("", response_model=list[VizOut], summary="全部可视化组件（管理）")
def list_all(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(VizComponent).order_by(VizComponent.updated_at.desc()).all()


@router.post(
    "", response_model=VizOut, status_code=status.HTTP_201_CREATED, summary="创建组件"
)
def create_viz(
    payload: VizCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    _ensure_slug_free(db, payload.slug)
    viz = VizComponent(
        slug=payload.slug,
        name=payload.name,
        source=payload.source,
        compiled=payload.compiled,
        style=payload.style,
    )
    db.add(viz)
    db.commit()
    db.refresh(viz)
    return viz


@router.put("/{viz_id}", response_model=VizOut, summary="更新组件")
def update_viz(
    viz_id: int,
    payload: VizUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    viz = get_or_404(db, VizComponent, viz_id, "组件不存在")
    data = payload.model_dump(exclude_unset=True)
    if "slug" in data and data["slug"] is not None:
        _ensure_slug_free(db, data["slug"], exclude_id=viz.id)
    for key, value in data.items():
        if value is not None:
            setattr(viz, key, value)
    db.commit()
    db.refresh(viz)
    return viz


@router.delete("/{viz_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除组件")
def delete_viz(
    viz_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    viz = get_or_404(db, VizComponent, viz_id, "组件不存在")
    db.delete(viz)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- 公开：按 slug 取（放最后）----------


@router.get(
    "/{slug}", response_model=VizPublic, summary="按 slug 取组件（公开，供读者挂载）"
)
def get_viz(slug: str, db: Session = Depends(get_db)):
    viz = db.query(VizComponent).filter(VizComponent.slug == slug).first()
    if viz is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "组件不存在")
    return viz
