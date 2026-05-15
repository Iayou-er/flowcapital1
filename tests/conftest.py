"""共享 fixtures"""
import os
import sys

# 必须在任何 backend 导入之前设置，因为 engine.py 在模块级别读取 DATABASE_URL
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENABLE_AUTH"] = "true"
os.environ["API_KEY"] = "test-api-key-for-ci"

# 确保项目根在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(scope="session")
def event_loop():
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db():
    from backend.database.db_manager_async import db_async
    await db_async.init_db()
    yield db_async


@pytest.fixture
async def client():
    from httpx import AsyncClient, ASGITransport
    from backend.api.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
