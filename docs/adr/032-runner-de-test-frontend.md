# ADR-032 — Runner de test frontend : Vitest, et un noyau de tests sur les coutures

**Date :** 2026-08-11 · **Statut :** accepté

## Contexte

Le frontend n'avait **aucun runner de test** : ni Vitest, ni Jest, aucun `*.test.ts(x)`. Cette
absence vient de coûter un défaut livré et resté invisible pendant plusieurs phases, décrit en
§Conséquences d'ADR-031 : `PrecheckReview` initialisait ses cases avec `useState(suggestion.…)` à
sa première monture, qui a lieu **avant** l'arrivée du rapport (la requête du document n'est
activée qu'à `awaiting_review`). La proposition du precheck n'existait donc que dans le badge
affiché à côté — le mécanisme était cosmétique depuis sa livraison, sur `heavy_ocr` comme sur
`optimize_for_tables`.

Ni la lecture du code, ni `tsc --noEmit`, ni `eslint` ne pouvaient le montrer : le code était
typé, cohérent, et sans faute de style. Il a fallu piloter un navigateur sur l'application réelle.
C'est exactement le trou que le backend n'a pas, où « une feature sans test n'est pas terminée »
(CONTRIBUTING.md §6) tient depuis la Phase 1.

## Décision

**Vitest + React Testing Library + jsdom**, avec un noyau de tests délibérément petit, placé sur
les **coutures asynchrones** plutôt que sur les fonctions pures.

**1. Vitest plutôt que Jest — une seule chaîne de build.** Vitest réutilise la configuration, le
pipeline de transformation et l'alias `@` déjà résolus par Vite ; Jest imposerait une seconde
chaîne à maintenir en parallèle pour l'ESM, le TSX et les alias, qui dériverait de la première au
premier changement. Compatibilité vérifiée, pas supposée : Vitest 4.1.10 déclare `vite: ^8.0.0`
en peer (le dépôt est en Vite 8.1.5), RTL 16.3.2 déclare `react: ^19.0.0` (React 19.2.8).

**2. Le bloc `test` vit dans `vite.config.ts`, pas dans un `vitest.config.ts`.** Un fichier séparé
prendrait la _priorité_ sur `vite.config.ts` au lieu de s'y ajouter : les tests perdraient l'alias
`@` et le plugin React. `defineConfig` est donc importé de `vitest/config`, qui réexporte celui de
Vite en acceptant la clé `test`.

**3. Tests co-localisés — `X.test.tsx` face à `X.tsx`.** C'est la lecture la plus stricte du
« miroir » qu'impose CONTRIBUTING.md §5 au backend : même dossier, même nom de base, plutôt qu'un arbre
parallèle qu'il faudrait resynchroniser à chaque renommage. Conséquence pratique décisive : les
fichiers de test héritent sans divergence possible du `include` de `tsconfig.json` (`tsc --noEmit`
les typecheck) et des blocs `src/**` d'`eslint.config.js` (globals navigateur, règles React) —
**aucune ligne de configuration ESLint ou TypeScript n'a eu à changer**. Le support commun
(`renderWithProviders`, fixtures, requêtes DOM) vit dans `src/test/`. Les tests ne sont importés
par rien, donc jamais embarqués dans le bundle (vérifié sur `dist/`).

**4. `globals: false`.** Chaque fichier importe `describe`/`it`/`expect` de `vitest` : rien à
déclarer côté ESLint ni côté `types` de `tsconfig.json`. Contrepartie assumée et branchée à la
main dans `src/test/setup.ts` : l'auto-nettoyage de Testing Library ne s'installe que s'il trouve
un `afterEach` **global**, sinon le DOM d'un test fuiterait dans le suivant.

**5. Le noyau de tests porte sur la couture, pas sur la fonction pure.** Un test unitaire de
`suggestedDecision` **n'aurait pas attrapé** le défaut : cette fonction était correcte. S'en
contenter aurait recréé l'angle mort qu'on cherche à supprimer. Le test de non-régression monte
donc `JobTracker` sur un `fetch` piloté par le test, qui livre le job d'abord et le rapport
**ensuite** — et vérifie que la case est `checked === true` après cette arrivée. Il ne pouvait pas
vivre dans `PrecheckReview.test.tsx` : le correctif est la `key` posée par le parent, et
`PrecheckReview.tsx` est identique avant et après le correctif.

**6. Le test a été vu rouge avant d'être vu vert.** Même méthode qu'ADR-028 §5 : la `key` a été
temporairement ramenée à sa forme d'avant correctif (`key={documentId}`), les deux tests de
`JobTracker.test.tsx` ont échoué sur les assertions `checked` attendues, puis la `key` a été
restaurée et la suite est repassée verte. Un test de non-régression dont on n'a jamais vu l'échec
ne prouve rien.

## Ce qu'on ne teste pas

- **Aucun objectif de couverture chiffré**, et aucun outil de couverture installé. Cinq tests qui
  tiennent valent mieux qu'une couverture large et fragile ; un pourcentage pousse à écrire des
  tests là où c'est facile, c'est-à-dire là où le défaut d'ADR-031 n'était pas.
- **Aucun test de rendu visuel** (capture d'écran, comparaison de pixels, styles calculés). Les
  CSS Modules ne sont même pas évalués sous Vitest : les tests interrogent le DOM accessible
  (rôles, libellés, état `checked`), jamais l'apparence.
- **Pas de test de bout en bout ni de navigateur piloté.** Ce qui a trouvé le défaut d'origine
  garde sa place, mais reste une vérification manuelle : ce runner ne prétend pas la remplacer.
- **Pas d'épinglage de la copie.** Les assertions vont chercher les libellés dans le dictionnaire
  `fr` plutôt que de recopier des chaînes d'interface, qui bougent sans que rien ne casse.

## Conséquences

- `npm test` (une passe) et `npm run test:watch` s'ajoutent aux scripts existants. `npm run lint`,
  `npm run typecheck` et `npm run build` restent verts, fichiers de test compris.
- CONTRIBUTING.md §7 pose désormais l'attente équivalente au §6 côté frontend. Sans elle, le runner
  cesserait de servir en trois phases.
- Dépendances de développement ajoutées : `vitest`, `jsdom`, `@testing-library/react`,
  `@testing-library/dom`, `@testing-library/user-event`. Aucune dépendance de production.
- `jsdom` est résolu en **29.1.1 et non 30.x**, et ce n'est pas un choix : jsdom 30 exige un Node
  `^22.22.2 || ^24.15.0 || >=26`, la machine tourne en Node 24.11. À relire le jour d'une montée
  de Node.
- L'installation a dû passer par `--legacy-peer-deps`, **pour une raison antérieure à cette
  ADR** : `openapi-typescript@7.13.0` déclare `typescript@^5.x` en peer alors que le dépôt est en
  TypeScript 6.0.3. `npm install` échouait déjà sans argument avant ce travail.
- Angle mort restant, constaté en écrivant les tests et **non corrigé** (hors mandat) :
  `suggestedDecision` ne filtre `optimizeForTables` sur aucun format — il ne tient que parce que
  la façade ne rapporte jamais `table_count > 0` hors PDF et tableur (`_unanalyzed`,
  `app/ingestion/precheck/analyzer.py`, laisse les compteurs neutres sur une image). Couplage
  implicite entre deux fichiers de deux dépôts, qu'aucun des deux ne déclare.
