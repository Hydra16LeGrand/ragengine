# ADR-008 — Fondations visuelles : jetons sémantiques, CSS Modules, palette pétrole

**Date :** 2026-07-25 · **Statut :** accepté (la Phase 6 confirme et étend, ne refonde pas)

## Contexte
CONTRIBUTING.md §7 exige une identité visuelle propre et interdit le rendu « template IA » générique. Trois choix devaient être posés avant tout composant, sous peine d'être très coûteux à reprendre : la mécanique de theming, la convention de styling, et la direction chromatique.

## Décision

**Jetons à deux niveaux.** Une palette brute (`--rag-petrol-600`, `--rag-neutral-50`…) est remappée sur des jetons **sémantiques** (`--color-canvas`, `--color-surface`, `--color-text-muted`, `--color-accent`, `--color-focus-ring`…). Les composants ne consomment QUE les jetons sémantiques : changer de thème est une simple redéclaration, et aucune valeur chromatique brute n'apparaît dans un composant. Les thèmes sont portés par `data-theme` sur `<html>`, posé par un script inline avant React (aucun flash au chargement), avec trois modes : clair, sombre, système.

**CSS Modules** (`*.module.css` colocalisés, un dossier par composant, composition via `cx()`). Retenu contre les alternatives runtime : support natif de Vite, zéro coût à l'exécution, portée locale garantie, et surtout du vrai CSS — ce qui rend mécanique le fait que `var(--…)` reste le seul vocabulaire visuel.

**Palette pétrole** (`#185c6b` en clair, `#6db1bf` en sombre) sur neutres légèrement chauds. Teinte froide et minérale, registre de l'encre technique et de l'archivage — délibérément à l'opposé du violet/indigo saturé des templates générés. Typographie sans-serif géométrique 100 % locale (Avenir Next / Futura / Jost / Poppins / URW Gothic, repli système) : **aucune ressource distante**, contrainte de souveraineté et de fonctionnement hors-ligne.

## Conséquences
- Contrastes mesurés : AA au minimum sur toutes les paires courantes, AAA sur le texte principal et l'accent (7,55:1 en clair, 7,27:1 en sombre). Toute nouvelle paire de jetons doit être vérifiée de la même façon.
- Un composant qui écrit une couleur en dur est un défaut de revue, pas un détail.
- La Phase 6 étend ce socle (composants riches, mise en page du dashboard) sans le refonder.
- Aucune librairie de composants tierce n'entrera : les composants sont maison, par construction (CONTRIBUTING.md §7).
