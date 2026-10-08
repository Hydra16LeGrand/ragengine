"""`FakeRerankerAdapter` : classement déterministe, et volontairement distinct du passthrough."""

from __future__ import annotations

from app.providers.rerankers.base import RerankerPort
from app.providers.rerankers.fake import FakeRerankerAdapter

QUERY = "requête de test"
DOCUMENTS = ["a", "b", "c", "d", "e"]


def test_fake_satisfies_the_reranker_port() -> None:
    """Le fake est substituable à tout adaptateur réel."""
    assert isinstance(FakeRerankerAdapter(), RerankerPort)


async def test_results_are_sorted_by_decreasing_score() -> None:
    """Contrat du port : la tête de liste est le meilleur candidat."""
    results = await FakeRerankerAdapter().rerank(QUERY, DOCUMENTS)

    scores = [result.score for result in results]
    assert scores == sorted(scores, reverse=True)


async def test_every_candidate_is_scored_exactly_once() -> None:
    """Aucune position perdue ni dupliquée."""
    results = await FakeRerankerAdapter().rerank(QUERY, DOCUMENTS)

    assert sorted(result.index for result in results) == list(range(len(DOCUMENTS)))


async def test_the_ranking_reverses_the_input_order() -> None:
    """Un fake indiscernable du passthrough laisserait passer un service qui l'ignore."""
    results = await FakeRerankerAdapter().rerank(QUERY, DOCUMENTS)

    assert [result.index for result in results] == list(reversed(range(len(DOCUMENTS))))


async def test_scoring_is_deterministic() -> None:
    """Deux instances, deux appels, le même classement."""
    first = await FakeRerankerAdapter().rerank(QUERY, DOCUMENTS)
    second = await FakeRerankerAdapter().rerank("une autre requête", DOCUMENTS)

    assert first == second


async def test_top_n_keeps_the_best_candidates() -> None:
    """La troncature s'applique après le classement, pas avant."""
    results = await FakeRerankerAdapter().rerank(QUERY, DOCUMENTS, top_n=2)

    assert [result.index for result in results] == [4, 3]


async def test_empty_candidates_yield_no_result() -> None:
    """Cas limite : une recherche sans candidat n'est pas une erreur."""
    assert await FakeRerankerAdapter().rerank(QUERY, []) == []
