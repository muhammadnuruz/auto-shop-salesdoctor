"""Client IP resolution. X-Forwarded-For is honoured only from configured trusted proxies."""

import ipaddress
from functools import lru_cache

from starlette.requests import Request


@lru_cache(maxsize=32)
def _networks(spec: tuple[str, ...]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    out = []
    for item in spec:
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return tuple(out)


def _is_trusted(ip: str, nets: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in nets)


def client_ip(request: Request, trusted_proxies: list[str]) -> str:
    peer = request.client.host if request.client else "unknown"
    nets = _networks(tuple(trusted_proxies))
    if not nets or not _is_trusted(peer, nets):
        return peer
    header = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in header.split(",") if h.strip()]
    # walk from the right: the first hop that is not one of our proxies is the real client
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return peer
        if not _is_trusted(hop, nets):
            return hop
    return hops[0] if hops else peer


def rate_buckets(ip: str | None) -> tuple[str, str]:
    """Rate-limit keys for an address: (primary, subnet).

    IPv4: the address itself and its /24. IPv6: the /64 (one subscriber owns a whole /64, so
    per-address keys are trivially evaded) and the /48.
    """
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return (ip or "unknown", ip or "unknown")
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        else:
            return (
                str(ipaddress.ip_network(f"{addr}/64", strict=False)),
                str(ipaddress.ip_network(f"{addr}/48", strict=False)),
            )
    return str(addr), str(ipaddress.ip_network(f"{addr}/24", strict=False))
