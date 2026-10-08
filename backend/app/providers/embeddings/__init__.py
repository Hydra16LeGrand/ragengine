"""Port `Embedding` et ses adaptateurs (modèles locaux multilingues, APIs tierces).

Les modèles locaux (défaut : BGE-M3) ne sont jamais chargés dans le process
FastAPI : ils sont servis par le service d'inférence GPU dédié, seul détenteur du
budget VRAM (CONTRIBUTING.md §2, ADR-002).
"""
