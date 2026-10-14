from app.core.config import Settings


def test_password_is_secret() -> None:
    s = Settings(SD_PASSWORD="hunter2")
    assert "hunter2" not in repr(s)
    assert "hunter2" not in str(s)
    assert s.SD_PASSWORD.get_secret_value() == "hunter2"


def test_defaults() -> None:
    s = Settings()
    assert s.SD_MAX_RETRIES == 3
    assert "Mozilla" in s.SD_USER_AGENT
