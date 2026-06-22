"""FastAPI 依赖项：数据库会话、当前登录用户。"""
from collections.abc import Generator

import jwt
from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.database import SessionLocal
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效或过期的凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        username = payload.get("sub")
        if not username:
            raise credentials_exc
    except jwt.PyJWTError:
        raise credentials_exc

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exc
    return user


def _resolve_user(raw_token: str | None, db: Session) -> User:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效或过期的凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not raw_token:
        raise credentials_exc
    try:
        payload = decode_token(raw_token)
        username = payload.get("sub")
        if not username:
            raise credentials_exc
    except jwt.PyJWTError:
        raise credentials_exc
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exc
    return user


def get_current_user_flexible(
    request: Request,
    token: str | None = Query(None),
    db: Session = Depends(get_db),
) -> User:
    """从 `?token=` 或 Authorization 头取 JWT。

    供 <img>/下载链接等浏览器直接发起、无法带 header 的 GET 请求使用。
    """
    raw = token
    if not raw:
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            raw = header[7:]
    return _resolve_user(raw, db)
