import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cli
from app.core.security import verify_password
from app.models import AuditLog


async def test_add_superadmin(session: AsyncSession) -> None:
    user = await cli.add_superadmin(session, " Boss ", "a-long-passphrase", "The Boss")
    await session.commit()
    assert user.username == "boss" and user.role == "superadmin" and user.active
    assert user.must_change_password is False
    assert (
        user.password_hash.startswith("$argon2id$")
        and "a-long-passphrase" not in user.password_hash
    )
    assert verify_password(user.password_hash, "a-long-passphrase")
    a = (
        await session.scalars(
            select(AuditLog).where(AuditLog.action == "admin_user.create_superadmin")
        )
    ).one()
    assert a.actor_type == "system" and "passphrase" not in str(a.after)


async def test_add_superadmin_rejects_duplicates_and_weak_input(session: AsyncSession) -> None:
    await cli.add_superadmin(session, "boss", "a-long-passphrase")
    with pytest.raises(ValueError, match="already exists"):
        await cli.add_superadmin(session, "BOSS", "a-long-passphrase")
    with pytest.raises(ValueError, match="at least"):
        await cli.add_superadmin(session, "other", "short")
    with pytest.raises(ValueError, match="username"):
        await cli.add_superadmin(session, "bad name!", "a-long-passphrase")


def test_password_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", "from-the-environment")
    assert cli._password() == "from-the-environment"


def test_password_prompt_requires_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    answers = iter(["one-passphrase-x", "two-passphrase-y"])
    monkeypatch.setattr("getpass.getpass", lambda _p="": next(answers))
    with pytest.raises(ValueError, match="do not match"):
        cli._password()


def test_main_reports_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", "short")
    assert cli.main(["create-superadmin", "--username", "boss"]) == 1
    assert "error:" in capsys.readouterr().err
