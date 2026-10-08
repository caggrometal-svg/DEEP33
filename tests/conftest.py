import pytest

import backend.main as main


@pytest.fixture(autouse=True)
def authenticated_backend_test_user(monkeypatch):
    async def fake_authenticated_user_id(request):
        user_id = "00000000-0000-4000-8000-000000000001"
        request.state.user_id = user_id
        return user_id

    monkeypatch.setattr(main, "authenticated_user_id", fake_authenticated_user_id)
