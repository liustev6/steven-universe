import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings():
    return Settings(VOICE=False)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client
