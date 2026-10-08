# ADR-029 — Maintien de `Plain Text` par défaut : arbitrage qualité de recherche vs ordre des fragments

**Date :** 2026-08-08 · **Statut :** accepté, **révisé par ADR-031** (2026-08-11) — son motif
(meilleure qualité de texte sous `Plain Text`) est tombé avec ADR-030, mesures à l'appui ; le
défaut de ce document reste celui du contrat d'API et du moteur, mais l'écran d'aiguillage propose
désormais `DeepDOC` coché sur tout PDF. Le raisonnement ci-dessous était correct au moment où il a
été tenu et n'est pas réécrit.

## Contexte

Tests manuels d'ingestion sur documents réels (base « TEST PIPELINE ») ont remonté quatre défauts :
ordre des fragments non préservé dans le panneau de consultation, phrases coupées en plein milieu
entre deux fragments, doubles espaces systématiques, formatage difficile à lire. Investigation
directe contre l'instance RAGFlow réelle (pas supposée) :

1. **Ordre non préservé** : `GET /datasets/{id}/documents/{doc}/chunks` trie côté moteur par
   `chunk_order_int, page_num_int, top_int, create_timestamp_flt`, mais sous
   `layout_recognize: "Plain Text"` (défaut actuel sur tout document à couche texte propre, ADR-023),
   aucun des trois premiers champs n'est jamais renseigné — l'analyse de mise en page est sautée.
   Le tri retombe sur un timestamp de création si fin que des fragments créés à quelques centaines
   de microsecondes d'écart arrondissent au même flottant : égalité totale, ordre non pertinent.
   **Corrigé côté RAGEngine** (`list_document_chunks`, commit `3b939c3`) : retri par position quand
   elle existe. Sans effet sous `Plain Text`, qui n'en produit jamais — documenté honnêtement dans
   le code plutôt que masqué.
2. **Doubles espaces** : `pypdf.extract_text()` (utilisé en interne par RAGFlow sous `Plain Text`)
   insère des doubles espaces sur du texte justifié — 13,7 % à 97,6 % des espacements inter-mots
   selon le document réel testé. **Corrigé** (commit `f5b45e0`, normalisation côté RAGEngine, jamais
   sur les sauts de ligne ni le contenu de type tableau) — vérifié à 0 % après correctif sur le même
   document. Ce coût est donc neutralisé, indépendamment du mode choisi.
3. **Phrases coupées mi-chunk** : `PlainParser` (mode `Plain Text`) découpe le PDF en sections une
   par ligne physique ; le délimiteur de découpage n'entre en jeu que si une section dépasse
   `chunk_token_num` (512 par défaut), ce qu'une ligne PDF (~15-25 tokens) atteint rarement. Le
   délimiteur est donc **un levier inopérant** sous ce mode (mesuré, aucun effet en ajoutant `.`/`;`).
   Non corrigé — corriger réellement demanderait soit d'abandonner `Plain Text`, soit un
   re-regroupement de lignes en paragraphes en amont côté RAGEngine (tension ADR-001).

## Étude chiffrée — six métriques, corpus synthétique + documents réels

`measure_deepdoc_vs_plaintext_real_docs.py` (outil interne) (commit `948bc65`), résultats bruts dans
`deepdoc-vs-plaintext-real-docs-20260808-200620` (mesure interne). Le corpus
synthétique de la Phase 11 s'est révélé **aveugle** aux défauts 1 et 3 (ils n'y apparaissent pas ou
sont négligeables) — seuls deux documents réels de l'utilisateur, rapatriés transitoirement pour
la mesure puis effacés, les révèlent.

| Métrique | `Plain Text` | `DeepDOC` |
|---|---|---|
| Fiabilité de position (ordre exploitable) | **0 % — sur les 6 documents, sans exception** | 100 % |
| Coupure mi-phrase (2 documents réels) | 33-60 % des fragments | 0 % |
| Doubles espaces | neutralisé par le correctif (`f5b45e0`), quel que soit le mode | idem |
| Mots reconnus / couverture source / entrelacement colonnes | **meilleur** (+0,6 à +12,3 pt / +0 à +26,1 pt / −1,6 à −28,8 pt) | moins bon |

Reproduit quasi à l'identique les faits 10-11 de la Phase 11 sur `02-pdf-multi-column-cv-fr.pdf` —
bonne validation croisée de la méthode. Aucune corrélation exploitable trouvée entre les défauts et
un type de document identifiable dans ce panel de 6 (un routage plus fin n'a pas de critère évident
à ce stade).

## Décision

**`layout_recognize` reste piloté par `is_likely_scanned` (ADR-023 inchangée) — `Plain Text` reste
le défaut sur tout document à couche texte propre.** Décision utilisateur (2026-08-08), avec
l'arbitrage suivant explicitement accepté : la qualité de texte pour la recherche (l'objectif
d'origine de la Phase 11 — accents, couverture, entrelacement de colonnes) prime sur un ordre de
fragments fiable et l'absence de coupure mi-phrase dans le panneau de consultation. Le coût
d'espacement est neutralisé indépendamment de ce choix (commit `f5b45e0`).

**Conséquences assumées, pas des bugs à rouvrir :**
- Le panneau de consultation des fragments (« Voir les fragments ») ne restitue jamais l'ordre
  correct d'un document traité en `Plain Text` — c'est structurel au mode, pas un défaut du tri
  ajouté par `3b939c3`.
- Des fragments continuent de couper des phrases en plein milieu sous `Plain Text` — le délimiteur
  de découpage n'a aucune prise sur ce mode.

**Non tranché, à revisiter si besoin réel** : un routage plus fin que `is_likely_scanned` (ex. basé
sur un signal qui distinguerait les documents où la coupure mi-phrase est réellement pénalisante)
reste une piste ouverte, mais le panel de 6 documents de cette étude est trop petit pour en définir
les critères. Ne pas relancer cette étude sans un corpus de documents réels significativement plus
large.
