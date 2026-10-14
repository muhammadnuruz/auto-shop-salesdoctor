"""Management CLI: ``python -m app.cli create-superadmin --username X``."""

import argparse
import asyncio
import getpass
import os
import re
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password, password_problem
from app.db.session import create_engine, create_sessionmaker
from app.models import AdminUser
from app.services import audit

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,64}$")


async def add_superadmin(
    db: AsyncSession, username: str, password: str, full_name: str = ""
) -> AdminUser:
    """Create an active superadmin (no forced password change); caller commits."""
    username = username.strip().lower()
    if not USERNAME_RE.match(username):
        raise ValueError("username must match [a-z0-9._-]{3,64}")
    problem = password_problem(password, username)
    if problem:
        raise ValueError(problem)
    if await db.scalar(select(AdminUser.id).where(AdminUser.username == username)):
        raise ValueError(f"user '{username}' already exists")
    user = AdminUser(
        username=username,
        full_name=full_name,
        password_hash=hash_password(password),
        role="superadmin",
    )
    db.add(user)
    await db.flush()
    audit.record(
        db,
        actor_type="system",
        actor_id=None,
        action="admin_user.create_superadmin",
        entity_type="admin_user",
        entity_id=user.id,
        after={"username": username, "role": "superadmin", "via": "cli"},
    )
    return user


async def create_superadmin(username: str, password: str, full_name: str = "") -> int:
    engine = create_engine(get_settings().DATABASE_URL)
    try:
        async with create_sessionmaker(engine)() as db:
            user = await add_superadmin(db, username, password, full_name)
            await db.commit()
            return user.id
    finally:
        await engine.dispose()


def _password() -> str:
    env = os.environ.get("ADMIN_PASSWORD")
    if env:
        return env
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise ValueError("passwords do not match")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-superadmin", help="create a superadmin account")
    p.add_argument("--username", required=True)
    p.add_argument("--full-name", default="")
    args = parser.parse_args(argv)
    try:
        user_id = asyncio.run(create_superadmin(args.username, _password(), args.full_name))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"superadmin '{args.username.strip().lower()}' created (id={user_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
