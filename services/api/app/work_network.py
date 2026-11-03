"""Explicit public-domain network grants with a fixed private-address deny floor."""

import asyncio
import re

from opensandbox.models.sandboxes import NetworkPolicy, NetworkRule

from app.safe_http import public_addresses

# Deny non-public IPv4 ranges even when a permitted domain changes its DNS answer.
# IPv6 is also disabled by the deployed egress server; the floor documents both families.
DENIED = [
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "::/128",
    "::1/128",
    "::ffff:0:0/96",
    "64:ff9b:1::/48",
    "100::/64",
    "2001::/23",
    "2001:db8::/32",
    "2002::/16",
    "fc00::/7",
    "fe80::/10",
    "ff00::/8",
]


def domain(value: str) -> str:
    result = value.strip().lower().encode("idna").decode("ascii")
    labels = result.split(".")
    if (
        len(result) > 253
        or len(labels) < 2
        or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
    ):
        raise ValueError("请填写完整域名，不含协议、路径、端口、IP 或通配符")
    if all(label.isdigit() for label in labels):
        raise ValueError("任务出网仅接受域名")
    return result


async def policy(domains: list[str]) -> NetworkPolicy:
    if not domains:
        return NetworkPolicy(defaultAction="deny")
    normalized = list(dict.fromkeys(domain(value) for value in domains))
    if len(normalized) > 10:
        raise ValueError("每次任务最多授权 10 个域名")
    # Resolve at admission and again at execution; packet-layer denies remain authoritative after rebinding.
    for host in normalized:
        await asyncio.to_thread(public_addresses, host)
    rules = [NetworkRule(action="deny", target=target) for target in DENIED]
    rules.extend(NetworkRule(action="allow", target=host) for host in normalized)
    return NetworkPolicy(defaultAction="deny", egress=rules)
