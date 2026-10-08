"""Proxys dont uvicorn honore `X-Forwarded-For` / `X-Forwarded-Proto` (ADR-052).

Exécuté une fois, au démarrage du conteneur `api` (`backend/Dockerfile`, `CMD`) :
`python -m app.core.trusted_proxies` écrit sur la sortie standard la valeur de
`uvicorn --forwarded-allow-ips`. Jamais importé par l'application elle-même.

**Ce qui est renvoyé** : le sous-réseau de l'interface qui porte la route par
défaut du conteneur (le réseau Docker `ragengine`, où vivent Traefik et
`cloudflared`), **moins l'adresse de sa passerelle**. Uvicorn parcourt
`X-Forwarded-For` de droite à gauche et retient la première adresse non
approuvée : chaque saut interne (Traefik, `cloudflared`) est ainsi sauté, et le
visiteur ajouté en fin de liste par l'edge Cloudflare est retenu — ce qu'un
client écrit plus à gauche n'est jamais atteint.

**Pourquoi la passerelle est exclue** : c'est l'adresse sous laquelle arrive
tout ce qui passe par un port publié sur l'hôte (NAT hairpin, ADR-040). L'exclure
empêche un appelant de l'hôte de choisir son IP par un en-tête forgé, et garde
l'invariant « l'Internet n'entre que par `traefik:80` sur le réseau Docker » :
un `cloudflared` pointé sur un port publié ferait arriver tout l'Internet sous
cette seule IP, visiblement, au lieu d'un contournement silencieux.

**Pourquoi calculé plutôt qu'écrit en dur** : le sous-réseau est attribué par
Docker et diffère d'un environnement à l'autre (dev, prod) ; le figer exigerait
de recréer le réseau. Il ne change pas pendant la vie d'un réseau existant.

Hors conteneur (`/.dockerenv` absent), la route par défaut est l'infrastructure
réelle du serveur : on renvoie le défaut d'uvicorn (`127.0.0.1`). Dans un
conteneur, une table de routage illisible fait échouer le démarrage — jamais de
confiance élargie, ni de repli silencieux qui ferait partager une seule IP à
tout l'Internet.
"""

from __future__ import annotations

import ipaddress
import struct
import sys
from pathlib import Path

LOOPBACK_ONLY = "127.0.0.1"
"""Défaut d'uvicorn : aucun proxy approuvé hors de la boucle locale."""

_PROC_NET_ROUTE = Path("/proc/net/route")
_DOCKERENV_MARKER = Path("/.dockerenv")
_ANY = "00000000"
_ROUTE_COLUMNS = 8  # Iface Destination Gateway Flags RefCnt Use Metric Mask (…)


class TrustedProxyDetectionError(RuntimeError):
    """La table de routage ne permet pas de déterminer le réseau des proxys."""


def _decode_ipv4(hex_value: str) -> ipaddress.IPv4Address:
    # Le noyau imprime l'adresse (ordre réseau) lue comme un entier machine :
    # la réempaqueter en ordre machine (`=`) restitue les octets d'origine.
    return ipaddress.IPv4Address(struct.pack("=L", int(hex_value, 16)))


def trusted_networks(route_table: str) -> list[ipaddress.IPv4Network]:
    """Sous-réseau de l'interface par défaut, passerelle exclue.

    Raises:
        TrustedProxyDetectionError: pas de route par défaut, pas de route de
            lien sur son interface, ou sous-réseau non privé.
    """
    rows = [line.split() for line in route_table.splitlines()[1:]]
    rows = [row for row in rows if len(row) >= _ROUTE_COLUMNS]

    default = next((row for row in rows if row[1] == _ANY and row[7] == _ANY), None)
    if default is None:
        raise TrustedProxyDetectionError("aucune route par défaut dans la table de routage")
    interface, gateway = default[0], _decode_ipv4(default[2])

    link = next(
        (row for row in rows if row[0] == interface and row[1] != _ANY and row[2] == _ANY),
        None,
    )
    if link is None:
        raise TrustedProxyDetectionError(f"aucun sous-réseau direct sur l'interface {interface}")
    network = ipaddress.IPv4Network(f"{_decode_ipv4(link[1])}/{_decode_ipv4(link[7])}")

    if not network.is_private:
        raise TrustedProxyDetectionError(f"sous-réseau {network} non privé : jamais approuvé")
    if gateway not in network:
        return [network]
    return sorted(network.address_exclude(ipaddress.IPv4Network(f"{gateway}/32")))


def forwarded_allow_ips(
    *,
    route_table_path: Path = _PROC_NET_ROUTE,
    dockerenv_path: Path = _DOCKERENV_MARKER,
) -> str:
    """Valeur de `--forwarded-allow-ips` pour le process courant. Jamais `*`."""
    if not dockerenv_path.exists():
        return LOOPBACK_ONLY
    route_table = route_table_path.read_text(encoding="ascii")
    return ",".join(str(network) for network in trusted_networks(route_table))


def main() -> int:
    try:
        value = forwarded_allow_ips()
    except (OSError, ValueError, TrustedProxyDetectionError) as exc:
        sys.stderr.write(f"trusted_proxies: détection impossible — {exc}\n")
        return 1
    sys.stdout.write(f"{value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
