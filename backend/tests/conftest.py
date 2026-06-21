"""pytest 公共夹具：用独立的内存 SQLite + 覆盖 get_db，避免污染真实库。

注意：使用 TestClient(app) 而非 `with TestClient(app)`，以免触发 lifespan 在真实库建表。
表结构与种子数据由夹具自行创建。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.database import Base
from app.deps import get_db
from app.main import app
from app.models.user import User

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "secret123"


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield TestingSession
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    # 种子管理员
    seed = db_session()
    seed.add(
        User(username=ADMIN_USERNAME, password_hash=hash_password(ADMIN_PASSWORD))
    )
    seed.commit()
    seed.close()

    def override_get_db():
        db = db_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def admin_creds():
    return {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}


@pytest.fixture
def auth_headers(client):
    resp = client.post(
        "/api/auth/login",
        data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
