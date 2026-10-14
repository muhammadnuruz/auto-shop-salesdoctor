import pytest

from app.domain.permissions import (
    ALL_PERMISSIONS,
    ROLE_PERMISSIONS,
    effective_permissions,
    unknown_permissions,
)


def test_superadmin_has_everything_regardless_of_extras() -> None:
    assert effective_permissions("superadmin") == ALL_PERMISSIONS
    assert effective_permissions("superadmin", ["bogus"]) == ALL_PERMISSIONS


def test_required_permission_strings_exist() -> None:
    required = {
        "links.read", "links.write", "orders.read", "orders.write", "orders.cancel",
        "clients.read", "clients.write", "products.read", "products.write", "sync.run",
        "settings.write", "admins.manage", "audit.read", "reports.read",
    }  # fmt: skip
    assert required <= ALL_PERMISSIONS


@pytest.mark.parametrize(
    ("role", "has", "lacks"),
    [
        ("admin", "links.write", "admins.manage"),
        ("operator", "orders.cancel", "links.write"),
        ("operator", "clients.read", "clients.write"),
        ("viewer", "reports.read", "orders.write"),
        ("viewer", "sync.read", "sync.run"),
    ],
)
def test_role_defaults(role: str, has: str, lacks: str) -> None:
    perms = effective_permissions(role)
    assert has in perms and lacks not in perms


def test_extra_permissions_add_and_unknown_are_ignored() -> None:
    perms = effective_permissions("viewer", ["links.write", "nope"])
    assert "links.write" in perms and "nope" not in perms
    assert effective_permissions("ghost-role") == frozenset()


def test_roles_never_exceed_all_permissions() -> None:
    assert all(p <= ALL_PERMISSIONS for p in ROLE_PERMISSIONS.values())
    assert unknown_permissions(["links.read", "x.y", "a.b"]) == ["a.b", "x.y"]
