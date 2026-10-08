#!/usr/bin/env python3
"""Repointe les datasets RAGFlow existants vers le fournisseur TEI (ADR-051) — idempotent.

    ./venv/bin/python scripts/migrate_ragflow_embedding_provider.py --dry-run
    ./venv/bin/python scripts/migrate_ragflow_embedding_provider.py

**Pourquoi ce script existe.** RAGFlow stocke sur chaque dataset l'identifiant
complet du modèle d'embedding (`embd_id`, ex. `"bge-m3@Xinference"`), pas un
identifiant nu. La bascule Xinference → TEI (ADR-051) change ce suffixe :
`bge-m3@ragengine-inference-embedding@HuggingFace`. Les datasets créés **avant**
la bascule continuent donc de nommer un fournisseur qui n'existe plus, et toute
ingestion ou recherche sur ces bases échoue (`LookupError: Provider Xinference
not found ...`), alors qu'une base créée après fonctionne. Ce script ferme cette
couture.

**Aucune réindexation.** Le modèle servi est le même (BGE-M3, 1024 dimensions) :
seul le *mode de service* change (Xinference → TEI). Les vecteurs déjà indexés
restent valides — RAGFlow les range dans un champ nommé d'après la dimension
(`q_1024_vec`), pas d'après le fournisseur. Vérifié en conditions réelles
(2026-09-12) : après la mise à jour d'un dataset portant déjà des fragments,
`POST /retrieval` continue de renvoyer ces fragments, avec et sans reranking.

**Ce que RAGFlow accepte, vérifié et pas supposé.** `PUT /api/v1/datasets/{id}`
avec `{"embedding_model": ...}` ne pose aucune garde sur le nombre de fragments
existants : le seul contrôle est `verify_embedding_availability`, qui résout le
modèle auprès des fournisseurs enregistrés du tenant
(`api/apps/services/dataset_api_service.py::update_dataset` →
`api/utils/api_utils.py`). Un dataset de 3 fragments a été migré puis interrogé
avec succès avant l'écriture de ce script.

Prérequis : `scripts/bootstrap_ragflow.py` a déjà enregistré le fournisseur
`HuggingFace` et ses deux instances — sinon RAGFlow refuse la mise à jour, ce
qui est le bon comportement (on ne pointe pas un dataset vers un fournisseur
absent).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import httpx  # noqa: E402  — après l'ajustement de `sys.path` ci-dessus.
from bootstrap_ragflow import (  # noqa: E402
    BootstrapError,
    RagflowBootstrapClient,
    resolve_base_url,
)

from app.core.config import Settings  # noqa: E402
from app.providers.engine.ragflow.client import (  # noqa: E402
    HUGGINGFACE_EMBEDDING_INSTANCE_NAME,
    HUGGINGFACE_PROVIDER,
)

PAGE_SIZE: Final = 100
"""Taille de page maximale acceptée par `GET /datasets` (constaté : `page_size=200`
fait échouer la requête, `100` passe)."""


def target_embedding_model(settings: Settings) -> str:
    """Identifiant complet attendu par RAGFlow, tel que l'adaptateur le construit.

    Même composition que `RagflowEngineAdapter.ensure_dataset` — la forme à trois
    segments `<modèle>@<instance>@<fournisseur>` est obligatoire depuis ADR-051 :
    deux instances actives coexistent sous `HuggingFace`, et la forme courte
    (`<modèle>@<fournisseur>`) n'est plus résoluble.
    """
    return (
        f"{settings.embedding_model_id}"
        f"@{HUGGINGFACE_EMBEDDING_INSTANCE_NAME}@{HUGGINGFACE_PROVIDER}"
    )


def list_datasets(client: RagflowBootstrapClient) -> list[dict[str, Any]]:
    """Rapatrie tous les datasets du tenant, page par page."""
    datasets: list[dict[str, Any]] = []
    page = 1
    while True:
        _, data, _ = client.request("GET", f"/datasets?page={page}&page_size={PAGE_SIZE}")
        batch = [entry for entry in data or [] if isinstance(entry, dict)]
        datasets.extend(batch)
        if len(batch) < PAGE_SIZE:
            return datasets
        page += 1


def migrate(client: RagflowBootstrapClient, settings: Settings, *, dry_run: bool) -> int:
    """Repointe chaque dataset dont le fournisseur n'est pas déjà le bon.

    Idempotent : un dataset déjà à jour n'est pas touché, donc relancer ce script
    ne coûte qu'une lecture. Renvoie le nombre de datasets modifiés (ou qui
    l'auraient été en `--dry-run`).
    """
    target = target_embedding_model(settings)
    datasets = list_datasets(client)
    stale = [entry for entry in datasets if entry.get("embedding_model") != target]

    print(f"→ {len(datasets)} dataset(s) au total, {len(stale)} à repointer vers « {target} ».")
    if not stale:
        print("→ Rien à faire : tous les datasets nomment déjà le bon fournisseur.")
        return 0

    for entry in stale:
        dataset_id = str(entry.get("id"))
        name = str(entry.get("name"))
        current = entry.get("embedding_model")
        chunks = entry.get("chunk_count")
        if dry_run:
            print(
                f"  [dry-run] {name} ({dataset_id}) : {current} → {target} "
                f"[{chunks} fragment(s)]"
            )
            continue
        client.request(
            "PUT",
            f"/datasets/{dataset_id}",
            json_body={"embedding_model": target},
        )
        print(f"  {name} ({dataset_id}) : {current} → {target} [{chunks} fragment(s)]")

    verb = "seraient repointés" if dry_run else "repointés"
    print(f"→ {len(stale)} dataset(s) {verb}.")
    return len(stale)


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : lecture, puis mise à jour ciblée des seuls datasets obsolètes."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="URL de l'API RAGFlow vue depuis l'hôte (par défaut : RAGFLOW_BASE_URL, "
        "puis le port publié en boucle locale).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="N'écrit rien : liste seulement les datasets qui seraient repointés.",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    if settings.ragflow_api_key is None:
        print(
            "✗ RAGFLOW_API_KEY absente : lance d'abord `scripts/bootstrap_ragflow.py`.",
            file=sys.stderr,
        )
        return 1

    try:
        base_url = resolve_base_url(settings, args.base_url)
        print(f"→ RAGFlow joignable sur {base_url}.")
        with RagflowBootstrapClient(base_url) as client:
            client.authenticate(f"Bearer {settings.ragflow_api_key.get_secret_value()}")
            migrate(client, settings, dry_run=args.dry_run)
    except BootstrapError as error:
        print(f"✗ {error}", file=sys.stderr)
        return 1
    except httpx.HTTPError as error:
        print(f"✗ RAGFlow injoignable : {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
