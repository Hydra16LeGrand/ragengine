"""Chaîne de confiance des en-têtes de proxy (ADR-052).

La valeur calculée est éprouvée contre le vrai `ProxyHeadersMiddleware`
d'uvicorn, pas contre une réimplémentation : c'est son parcours de
`X-Forwarded-For` (de droite à gauche, première adresse non approuvée) qui
décide de `request.client.host`, donc de toutes les clés de plafond par IP.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

import pytest
from starlette.types import Message, Receive, Scope, Send
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.trusted_proxies import (
    LOOPBACK_ONLY,
    TrustedProxyDetectionError,
    forwarded_allow_ips,
    main,
    trusted_networks,
)

# Copie conforme de `/proc/net/route` du conteneur `ragengine-api` (réseau
# `172.18.0.0/16`, passerelle `172.18.0.1`), tabulations et remplissage compris.
_HEADER = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT"
_DOCKER_ROUTES = (
    f"{_HEADER}                                                       \n"
    "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0          \n"
    "eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0          \n"
)
_GATEWAY = "172.18.0.1"
_TRAEFIK = "172.18.0.12"
_CLOUDFLARED = "172.18.0.13"
_VISITOR = "203.0.113.7"


async def _never_receive() -> Message:
    raise AssertionError("aucun corps lu")


async def _never_send(message: Message) -> None:
    raise AssertionError("aucune réponse émise")


async def _client_seen_by_app(peer: str, x_forwarded_for: str | None) -> str:
    """`request.client.host` tel que l'application le verrait derrière uvicorn."""
    seen: dict[str, str] = {}

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen["host"] = scope["client"][0]

    headers = [] if x_forwarded_for is None else [(b"x-forwarded-for", x_forwarded_for.encode())]
    trusted = forwarded_allow_ips_for(_DOCKER_ROUTES)
    middleware = ProxyHeadersMiddleware(app, trusted_hosts=trusted)  # type: ignore[arg-type]
    scope = {"type": "http", "client": (peer, 40000), "headers": headers, "scheme": "http"}
    await middleware(scope, _never_receive, _never_send)  # type: ignore[arg-type]
    return seen["host"]


def forwarded_allow_ips_for(route_table: str) -> str:
    return ",".join(str(network) for network in trusted_networks(route_table))


def test_the_docker_subnet_is_trusted_except_its_gateway() -> None:
    networks = trusted_networks(_DOCKER_ROUTES)

    assert all(net.subnet_of(ipaddress.IPv4Network("172.18.0.0/16")) for net in networks)
    assert sum(net.num_addresses for net in networks) == 2**16 - 1
    assert not any(ipaddress.IPv4Address(_GATEWAY) in net for net in networks)
    assert any(ipaddress.IPv4Address(_TRAEFIK) in net for net in networks)


async def test_the_visitor_appended_by_the_edge_wins_over_a_forged_prefix() -> None:
    # Edge Cloudflare : ajoute le visiteur ; Traefik (pair approuvé) : ajoute
    # l'IP de cloudflared. Le client a forgé tout ce qui précède.
    xff = f"6.6.6.6, {_GATEWAY}, {_VISITOR}, {_CLOUDFLARED}"

    assert await _client_seen_by_app(_TRAEFIK, xff) == _VISITOR


async def test_two_visitors_get_two_distinct_client_addresses() -> None:
    first = await _client_seen_by_app(_TRAEFIK, f"198.51.100.1, {_CLOUDFLARED}")
    second = await _client_seen_by_app(_TRAEFIK, f"198.51.100.2, {_CLOUDFLARED}")

    assert (first, second) == ("198.51.100.1", "198.51.100.2")


async def test_a_host_caller_through_a_published_port_cannot_choose_its_address() -> None:
    # Port publié de l'API : le pair est la passerelle, jamais approuvée.
    assert await _client_seen_by_app(_GATEWAY, "198.51.100.9") == _GATEWAY
    # Port publié de Traefik : Traefik ajoute la passerelle en fin de liste.
    assert await _client_seen_by_app(_TRAEFIK, f"198.51.100.9, {_GATEWAY}") == _GATEWAY


async def test_an_internal_hop_without_forwarded_header_keeps_its_own_address() -> None:
    assert await _client_seen_by_app(_TRAEFIK, None) == _TRAEFIK


async def test_a_peer_outside_the_docker_network_is_never_trusted() -> None:
    assert await _client_seen_by_app("192.168.1.20", "198.51.100.9") == "192.168.1.20"


def test_outside_a_container_only_loopback_is_trusted(tmp_path: Path) -> None:
    value = forwarded_allow_ips(
        route_table_path=tmp_path / "absent", dockerenv_path=tmp_path / "absent"
    )

    assert value == LOOPBACK_ONLY


def test_inside_a_container_the_route_table_is_read(tmp_path: Path) -> None:
    (tmp_path / ".dockerenv").touch()
    (tmp_path / "route").write_text(_DOCKER_ROUTES, encoding="ascii")

    value = forwarded_allow_ips(
        route_table_path=tmp_path / "route", dockerenv_path=tmp_path / ".dockerenv"
    )

    assert value == forwarded_allow_ips_for(_DOCKER_ROUTES)
    assert "*" not in value


@pytest.mark.parametrize(
    "route_table",
    [
        f"{_HEADER}\n",
        f"{_HEADER}\neth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n",
        # Sous-réseau public (8.8.8.0/24) : jamais approuvé.
        f"{_HEADER}\neth0\t00000000\t01080808\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t00080808\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n",
    ],
    ids=["no-default-route", "no-link-route", "public-subnet"],
)
def test_an_unusable_route_table_is_refused(route_table: str) -> None:
    with pytest.raises(TrustedProxyDetectionError):
        trusted_networks(route_table)


def test_main_fails_loudly_when_detection_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(**_: object) -> str:
        raise TrustedProxyDetectionError("table vide")

    monkeypatch.setattr("app.core.trusted_proxies.forwarded_allow_ips", broken)

    assert main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "table vide" in captured.err
