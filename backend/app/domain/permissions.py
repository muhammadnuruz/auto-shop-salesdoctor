"""RBAC: permission strings and the default permission set of each role."""

from collections.abc import Iterable

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        "links.read",
        "links.write",
        "orders.read",
        "orders.write",
        "orders.cancel",
        "clients.read",
        "clients.write",
        "products.read",
        "products.write",
        "sync.read",
        "sync.run",
        "settings.write",
        "admins.manage",
        "audit.read",
        "reports.read",
    }
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "superadmin": ALL_PERMISSIONS,
    "admin": ALL_PERMISSIONS - {"admins.manage"},
    "operator": frozenset(
        {
            "orders.read",
            "orders.write",
            "orders.cancel",
            "clients.read",
            "products.read",
            "links.read",
        }
    ),
    "viewer": frozenset(
        {
            "links.read",
            "orders.read",
            "clients.read",
            "products.read",
            "reports.read",
            "sync.read",
        }
    ),
}


def effective_permissions(role: str, extra: Iterable[str] = ()) -> frozenset[str]:
    if role == "superadmin":
        return ALL_PERMISSIONS
    return ROLE_PERMISSIONS.get(role, frozenset()) | (frozenset(extra) & ALL_PERMISSIONS)


def unknown_permissions(perms: Iterable[str]) -> list[str]:
    return sorted(set(perms) - ALL_PERMISSIONS)
