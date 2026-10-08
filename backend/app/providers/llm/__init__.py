"""Port `LLM` et ses adaptateurs vers les APIs de génération tierces.

La génération est systématiquement externalisée (Anthropic, OpenAI) : aucun LLM
génératif ne tourne en local, le GPU étant réservé à l'extraction visuelle, aux
embeddings et au reranking (CONTRIBUTING.md §2).
"""
