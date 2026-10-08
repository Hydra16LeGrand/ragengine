# `src/components/` — bibliothèque UI maison

Composants **génériques et sans logique métier**. Tout ce qui connaît une base de connaissances,
un job d'ingestion ou un résultat de retrieval appartient à `src/features/`, pas ici.

> CONTRIBUTING.md §7 : identité visuelle propre. **Aucune librairie de composants tierce**
> (shadcn/ui, Radix, MUI, Chakra, Ant, Bootstrap), **pas de Tailwind**, **aucune ressource
> chargée depuis un CDN** (polices et icônes comprises — le déploiement doit fonctionner
> hors-ligne).

## Conventions

**1. Un dossier par composant**, en `PascalCase`, avec trois fichiers :

```
Button/
├── Button.tsx           # le composant, export nommé (jamais `export default`)
├── Button.module.css    # ses styles, colocalisés
└── index.ts             # ré-export public du dossier
```

L'import se fait toujours par le dossier : `import { Button } from '@/components/Button'`.

**2. Styles en CSS Modules** (`*.module.css`) — décision de Phase 0.

Pourquoi ce choix plutôt qu'une alternative :

- support natif de Vite, **zéro runtime** et zéro dépendance (contrairement au CSS-in-JS) ;
- portée locale garantie : pas de collision de noms, pas de convention BEM à faire respecter ;
- on écrit du **vrai CSS**, donc les jetons `var(--…)` de `src/design/` restent le seul
  vocabulaire visuel — ce qu'aucune approche utilitaire ne garantit ;
- la feuille reste lisible et diffable, ce qui compte pour la reprise de la direction
  artistique en Phase 6.

Règles associées :

- classes en `camelCase` (accès direct `styles.optionActive`) ;
- composition des classes via l'utilitaire `cx()` de `@/lib/cx` — obligatoire, car avec
  `noUncheckedIndexedAccess` un accès `styles[variant]` est typé `string | undefined` ;
- **aucune valeur brute** : ni couleur hexadécimale, ni `px` d'espacement, ni ombre écrite à la
  main. On consomme les jetons sémantiques (`var(--color-surface)`, `var(--rag-space-4)`…).
  Une valeur qui manque au système se rajoute dans `src/design/tokens.css`, pas dans le composant ;
- un composant ne fixe jamais ses marges externes : c'est le parent qui positionne. Il accepte
  en revanche `className` pour être ajustable.

**3. API des composants**

- props typées et exportées (`export interface ButtonProps extends ButtonHTMLAttributes<…>`) ;
- extension des attributs HTML natifs par défaut, `...rest` étalé sur l'élément racine ;
- pas de `any`, pas de `@ts-ignore` ;
- accessibilité incluse dès l'écriture : élément natif quand il existe (`<button>`, `<input
type="radio">`), état visible au clavier via `:focus-visible`, libellé accessible systématique.

**4. Libellés**

Aucune chaîne visible en dur : tout passe par `useT()` (`src/i18n/`). Un composant purement
présentationnel reçoit ses textes en props ; un composant qui affiche ses propres libellés
(comme `ThemeToggle`) les lit via `useT()`.

## Inventaire

| Composant          | Rôle                                                             | Phase |
| ------------------ | ---------------------------------------------------------------- | ----- |
| `Button`           | Bouton — `primary` / `secondary` / `ghost` / `danger`            | 0     |
| `ThemeToggle`      | Sélecteur de thème clair / sombre / système                      | 0     |
| `StatusDot`        | Pastille d'état décorative (succès, alerte, en cours…)           | 0     |
| `Card`             | Surface délimitée + `CardHeader` / `CardBody` / `CardFooter`     | 6     |
| `Field`            | Enveloppe de champ : libellé, aide, erreur, liaison ARIA         | 6     |
| `Input`            | Saisie sur une ligne (variantes `mono`, `prefix`)                | 6     |
| `Textarea`         | Saisie multiligne                                                | 6     |
| `Select`           | Liste déroulante sur `<select>` natif                            | 6     |
| `Checkbox`         | Case à cocher avec description                                   | 6     |
| `Badge`            | Étiquette compacte (état, rôle, visibilité)                      | 6     |
| `Skeleton`         | Bloc de chargement animé, à la forme du contenu attendu          | 6     |
| `Spinner`          | Activité ponctuelle **dans un bouton** uniquement                | 6     |
| `Dialog`           | Boîte de dialogue modale                                         | 6     |
| `Toast`            | Notifications empilées (`useToast`)                              | 6     |
| `EmptyState`       | État vide dessiné (illustrations maison)                         | 6     |
| `PageHeader`       | En-tête d'écran : fil d'Ariane, sur-titre, actions               | 6     |
| `Dropzone`         | Dépôt de fichiers : glisser-déposer **et** champ natif           | 7     |
| `StepProgress`     | Progression **par étapes franchies** (jamais un pourcentage)     | 7     |
| `SegmentedControl` | Bascule entre modes exclusifs, sur `<input type="radio">` natifs | 7     |

### Notes de conception (Phase 7)

- **`Dropzone`** garde l'`<input type="file">` natif dans le DOM, focalisable : l'ouverture au
  clavier, l'annonce vocale et le sélecteur système sur mobile sont ainsi gratuits. La zone
  réagit via `:focus-within`. Le survol de dépôt est **compté** (`dragenter`/`dragleave` se
  déclenchent aussi entre enfants) — sans compteur, la zone clignoterait.
- **`StepProgress`** n'accepte **aucun ratio**, délibérément : il est alimenté par une machine à
  états serveur qui ne publie pas d'avancement continu fiable (ADR-012). Une barre qui se
  remplirait régulièrement serait une invention de l'interface. Franchir une étape est un fait
  vérifiable ; c'est le seul que l'on montre.
- **`SegmentedControl`** s'appuie sur des radios natifs pour la même raison que `Select` sur le
  `<select>` natif : navigation par flèches, groupement annoncé et sémantique « un seul choix »
  corrects sans réimplémentation.
