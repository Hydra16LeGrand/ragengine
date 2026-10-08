"""`PassthroughReranker` : le Null Object du reranking.

C'est l'implémentation résolue lorsqu'une base a `reranker_model_id IS NULL`.
Deux garanties comptent pour les services : l'ordre d'entrée survit intact, et le
score n'invente aucune information.
"""

from __future__ import annotations

from app.core.config import Settings
from app.providers.rerankers.base import RerankerPort
from app.providers.rerankers.passthrough import PASSTHROUGH_SCORE, PassthroughReranker

QUERY = "Quel est le chiffre d'affaires ?"
DOCUMENTS = ["premier passage", "deuxième passage", "troisième passage", "quatrième passage"]


def test_passthrough_satisfies_the_reranker_port() -> None:
    """Substituable à un vrai reranker, sans branche conditionnelle côté service."""
    assert isinstance(PassthroughReranker(), RerankerPort)


def test_passthrough_is_built_without_any_configuration() -> None:
    """Le Null Object ne dépend de rien : ni secret, ni URL."""
    assert PassthroughReranker.from_settings(Settings()).capabilities.provider_id == "passthrough"


def test_passthrough_declares_itself_non_local() -> None:
    """Rien ne tourne sur le GPU : aucune VRAM à réserver (CONTRIBUTING.md §2)."""
    assert PassthroughReranker().capabilities.is_local is False


async def test_input_order_is_preserved() -> None:
    """Les positions ressortent dans l'ordre reçu, une et une seule fois chacune."""
    results = await PassthroughReranker().rerank(QUERY, DOCUMENTS)

    assert [result.index for result in results] == list(range(len(DOCUMENTS)))


async def test_scores_are_constant() -> None:
    """Un score constant dit « aucune pertinence n'a été calculée »."""
    results = await PassthroughReranker().rerank(QUERY, DOCUMENTS)

    assert {result.score for result in results} == {PASSTHROUGH_SCORE}


async def test_top_n_truncates_without_reordering() -> None:
    """`top_n` coupe la tête de liste, il ne reclasse rien."""
    results = await PassthroughReranker().rerank(QUERY, DOCUMENTS, top_n=2)

    assert [result.index for result in results] == [0, 1]


async def test_empty_candidates_yield_no_result() -> None:
    """Cas limite : une recherche sans candidat n'est pas une erreur."""
    assert await PassthroughReranker().rerank(QUERY, []) == []
