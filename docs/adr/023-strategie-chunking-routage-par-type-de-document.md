# ADR-023 — Qualité d'extraction : meilleur routage RAGFlow, pas de pré-traitement local ni de moteur externe

**Date :** 2026-08-07 · **Statut :** accepté

## Résumé (lire le corps si tu touches un de ces points)

- **Décision : option (a)** — corriger le routage `chunk_method`/`parser_config` par type de
  document dans `chunking_profile.py` (Étape 5 du plan interne). Rejetées : (b) pré-traitement
  local (tension directe avec ADR-001), (c) parseur externe (coût VRAM/service non justifié par les
  données), (d) changer de moteur (aucune limite intrinsèque du moteur identifiée).
- **Pourquoi ce n'est pas (b)/(c)/(d)** : les 3 étapes de mesure de la Phase 11 (plan interne,
  faits établis 1-12, matrice `chunk_method` × corpus) n'ont trouvé **aucun** défaut qui résiste à
  un meilleur réglage du moteur existant. Colonnes entrelacées, casse cassée, `resume` inexistant,
  `email` qui corrompt en silence : tous expliqués par un mauvais choix de configuration, jamais
  par une limite du moteur lui-même.
- **Le levier principal découvert n'était pas anticipé au départ de la Phase 11** :
  `parser_config.layout_recognize = "Plain Text"` bat `"DeepDOC"` sur tout document à couche texte
  propre (moins d'erreurs de casse, moins d'entrelacement, meilleure couverture) — `DeepDOC` reste
  nécessaire uniquement pour un vrai scan. Le mapping actuel de `chunking_profile.py` impose
  `DeepDOC` par défaut à tout, y compris aux documents propres : c'est la correction la plus
  impactante que l'Étape 5 doit porter, pas une simple case à cocher optionnelle.
- **Cette décision ne clôt pas la Phase 11** : l'Étape 4 (garde-fou qualité, un document dégradé ne
  doit jamais finir `completed` en silence) reste nécessaire même avec un routage parfait — aucun
  extracteur ne réussit 100 % des documents, et le meilleur routage trouvé laisse des cas
  imparfaits (ex. `email` sur PDF, `28-pdf-corrupted.pdf`).

## Contexte

Signalé par l'utilisateur après test d'un vrai CV : colonnes entrelacées, casse cassée
(`PosTGREsQL`, `Linkedln`), un chunk réduit à `"Q"` — indistinguable d'un RAG générique, alors que
l'expertise d'extraction est le seul produit de RAGEngine. Trois étapes de mesure ont suivi
(plan interne, Phase 11) : harnais de mesure + corpus de 29 documents fictifs (Étape 0),
diagnostic de causes (Étape 1), cartographie complète de 50 combinaisons `chunk_method`/
`parser_config` (Étape 2). Cette ADR consigne la décision d'architecture qui en découle,
conformément à l'arrêt obligatoire prévu à l'Étape 3.

## Décisions

**1. Option retenue : (a) meilleur routage RAGFlow.** Pleinement conforme à ADR-001 (RAGFlow reste
le moteur unique, on utilise ses briques internes « à fond », on ne les réimplémente pas) et à
coût faible (changement borné à `chunking_profile.py`, aucun nouveau service).

**2. Options rejetées, avec leur raison empirique précise :**
   - **(b) Pré-traitement local** (ex. reconstruire l'ordre de lecture des colonnes avant envoi au
     moteur) — rejeté parce que l'Étape 1 a montré que l'entrelacement de colonnes n'est **pas**
     une limite intrinsèque de DeepDOC mais une conséquence du mauvais `parser_config` : `Plain
     Text` réduit déjà l'entrelacement mesuré sur le même document. Réimplémenter la logique de
     lecture en colonnes contournerait une brique que RAGFlow sait déjà mieux faire une fois
     correctement configuré — exactement ce qu'ADR-001 interdit.
   - **(c) Parseur externe** (MinerU, PaddleOCR) — rejeté faute de défaut qui le justifie : aucune
     combinaison testée en Étape 2 n'a révélé une limite structurelle de DeepDOC/RAGFlow qu'un
     parseur externe résoudrait seul. Coût (nouveau service, empreinte VRAM à documenter dans un
     budget déjà partagé par BGE-M3 et le reranker, §2 CONTRIBUTING.md) non justifié par les données
     actuelles. Reste une option de repli si l'Étape 5 révèle un cas non couvert par (a).
   - **(d) Changer de moteur** — rejeté sans discussion approfondie : aucune des options (b)/(c)
     n'étant elle-même justifiée, remettre en cause le moteur (contredit ADR-001, coût très élevé)
     n'a pas lieu d'être évalué plus loin.

**3. Portée immédiate de la décision pour l'Étape 5** (implémentation, pas cette ADR) : le mapping
`chunking_profile.py` doit cesser d'imposer `DeepDOC` par défaut à tout document. Un document dont
le precheck a établi une couche texte propre (`is_likely_scanned = false`) doit recevoir
`layout_recognize = "Plain Text"` sauf override explicite (le levier « OCR lourd » du precheck
reste pertinent pour forcer `DeepDOC` sur un cas où l'heuristique se trompe) ; un document
probablement scanné reçoit `DeepDOC`. Détail de mapping et cas limites laissés à l'Étape 5, qui
dispose de la matrice complète (`explore-chunking-matrix-*` (mesure interne)).

**4. L'Étape 4 (garde-fou qualité) n'est pas rendue optionnelle par cette décision.** Le meilleur
routage trouvé n'atteint pas 100 % : `chunk_method=email` sur un PDF « réussit » avec un contenu
corrompu (couverture 2,2 %), `28-pdf-corrupted.pdf` finit `completed` avec 0 chunk. Ces deux cas
resteront possibles après l'Étape 5 tant que rien ne détecte et ne signale le résultat dégradé
après coup.

## Conséquences

- `backend/app/ingestion/chunking_profile.py` change de logique par défaut (Étape 5) : le choix de
  `layout_recognize` dépend désormais du résultat du precheck (`is_likely_scanned`), pas d'une
  valeur fixe. Impact direct sur le comportement observable de l'aiguillage utilisateur existant
  (§4 `docs/ingestion-engine-architecture.md`, à mettre à jour par l'Étape 5 en même temps que le
  code).
- Aucun nouveau service, aucune nouvelle dépendance VRAM — le budget §2 de CONTRIBUTING.md n'est pas
  affecté par cette ADR.
- (c) et (d) restent des options de repli documentées, pas closes définitivement : si l'Étape 5
  révèle un cas structurel que RAGFlow ne peut pas couvrir même bien configuré, cette ADR devra
  être révisée plutôt que contournée en silence.
- L'Étape 4 (garde-fou qualité) reste sur le chemin critique de la promesse produit
  (« extraction maximisée + qualité mesurée et signalée », plan interne) : elle est la seule
  ligne de défense contre les cas que même un routage correct ne résout pas.
