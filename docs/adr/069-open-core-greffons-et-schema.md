# ADR-069 — Open core : RAGEngine (cœur) et RAGPlatform (greffon), un seul schéma de base

**Date :** 2026-10-04 · **Statut :** accepté

## Résumé

- Chatbots publics, mode invité et administration sont un **greffon** (`platform`), exclus de l'export
  par suppression de dossiers : retirer du code est plus sûr qu'un drapeau, et rien n'est patché.
- Le cœur découvre les greffons sans les nommer ; sans greffon il exige une connexion partout.
- **Schéma de base unique** : colonnes `is_guest`, rôle `admin`, champs de publication restent, car
  une migration destructive en production est exclue ; elles sont inertes dans RAGEngine.
- `check.py` verrouille l'absence de trace (chemins, imports, routes, clés de libellés, variables).

## Contexte

Le dépôt privé (RAGPlatform, en production) est exporté vers un dépôt public, RAGEngine. Le
modèle voulu est *open core* : RAGEngine doit être **plus limité** que RAGPlatform. Trois
fonctionnalités sont propres à la plateforme hébergée : les **chatbots publics** (publication,
page publique, plafonds, clé LLM d'essai), le **mode invité** (essai sans compte, espaces
éphémères, élévation en compte, purge, quotas anonymes) et l'**administration** (tableau de bord,
routes `/admin`). Le principe imposé : on **retire du code**, jamais un drapeau ; et l'export doit
rester robuste à chaque synchronisation, sans patcher de code au cas par cas.

## Décision

1. **Un greffon, un dossier.** Le cœur découvre les greffons : `app.plugins.<nom>` côté backend
   (`app/core/plugins.py`, `pkgutil`), `src/plugins/<nom>/index.ts(x)` côté SPA
   (`import.meta.glob`). Le cœur ne nomme jamais un greffon, et un greffon dépend du cœur, jamais
   l'inverse. RAGPlatform est le greffon `platform` (`backend/app/plugins/platform`,
   `frontend/src/plugins/platform`, tests sous `tests/plugins/platform`).
2. **Points d'extension typés, comportement par défaut complet et sûr.** Backend : routeurs,
   admission de l'anonyme, portées de clé API, tâches Celery, étapes de maintenance, politique de
   financement LLM et compteurs, plafonds de création, champs de `KnowledgeBaseRead` et
   `PrincipalRead`. SPA : routes autonomes et de coquille, entrées de navigation, bandeau, badges
   d'identité, extra de la page de connexion, écran de compte éphémère, section et garde de la page
   d'une base, portées de clé API, libellés (dictionnaire du greffon). Sans greffon : tout exige
   une connexion, aucune route chatbot, invité ou `/admin`.
3. **L'export retire des dossiers.** `scripts/export-public.sh` exclut les trois dossiers du
   greffon et les ADR 046, 047, 048. Aucun fichier du cœur n'est patché ni remplacé pour cela ;
   `.env.example` ne porte plus que les variables du cœur, celles du greffon vivent dans
   `backend/app/plugins/platform/env.example`. `check.py` échoue si l'arbre public garde un
   chemin de plateforme, un import du greffon, une route, une clé de libellé ou une variable
   d'environnement correspondante.
4. **Un seul schéma de base, aucune migration destructive.** Les colonnes et tables liées aux
   fonctionnalités de la plateforme (`users.is_guest`, rôle `admin`, champs de publication de
   chatbot) restent dans le schéma et les migrations du cœur : la base de production n'est pas
   touchée, les migrations ne divergent pas entre les deux dépôts, et le cœur reste un
   sous-ensemble strict de la plateforme. Dans RAGEngine ces colonnes sont simplement inertes
   (aucun code ne crée d'invité ni ne publie de chatbot). Le rôle plateforme `admin` reste dans le
   RBAC du cœur (il voit toute base) ; seules l'interface et les routes `/admin` sont exclues.
5. **Plafonds de la clé serveur.** Le cœur conserve les plafonds par compte (chat, évaluation) et
   le coupe-circuit global quand la clé serveur finance un appel : `RAGENGINE_PLATFORM_LLM_*`
   garde son nom, car renommer casserait les `.env` déployés. Clé d'essai, plafonds de chatbot et
   plafonds anonymes sont dans le greffon.

## Conséquences

- RAGPlatform se comporte comme avant : même API, mêmes clés Redis, mêmes tâches, mêmes variables.
- Un nouveau code de plateforme va dans le greffon ; le cœur n'y gagne qu'un point d'extension
  quand une fonctionnalité en exige un.
- Le schéma de la base contient des vestiges inertes dans RAGEngine ; les retirer exigerait une
  migration destructive en production, refusée.
- Le test `test_core_without_plugins` (backend) et les tests du cœur seul (frontend) prouvent que
  le cœur tient sans greffon.

## Addendum — 2026-10-04 : réglages par défaut du greffon, marque, guide d'API, licence

Suite à la relecture d'un intégrateur externe : le cœur ne doit rien imposer de la démo hébergée.

- **Défauts de réglages fournis par le greffon** (`Plugin.settings_defaults`). `Settings` applique, aux seuls réglages que l'environnement ne renseigne pas, les valeurs du greffon ; un `.env` qui pose une variable (même vide) l'emporte. Le cœur seul n'a **aucun plafond ni modèle imposé** (`RAGENGINE_PLATFORM_LLM_*` à `None`), et sa marque est `RAGEngine`. RAGPlatform retrouve 20 messages, 24 évaluations, 10 $ et `claude-haiku-4-5` par ses défauts de greffon (`app/plugins/platform/llm_defaults.py`), sans modifier aucun `.env`. Détail et preuves : addendum 2026-10-04 d'ADR-054. Ce choix remplace le point 5 de la décision (les plafonds « de la clé serveur » restaient dans le cœur avec des valeurs par défaut). `plugins_overridden` vide le cache de `get_settings()` : le cœur seul, y compris `app.cli.openapi --core`, ne voit jamais les défauts d'un greffon.
- **Marque en réglage.** Backend : `RAGENGINE_APP_NAME` (défaut `RAGEngine`, titre de l'OpenAPI et de la page de référence), `RAGPlatform` fourni par le greffon. Frontend : `getAppName()` lit `VITE_APP_NAME` (variable de build, `--build-arg` du Dockerfile), sinon le nom du greffon (`FrontendPlugin.appName`), sinon `RAGEngine` ; tout libellé peut écrire `{appName}`. Choix du build plutôt que d'une valeur servie par l'API : aucune requête avant le premier rendu, aucune route publique de plus. Le titre statique d'`index.html` vient de la même variable ; la SPA le corrige à l'exécution quand un greffon fournit la marque. `check.py` échoue si « RAGPlatform » apparaît dans l'arbre public hors licence commerciale et ce document.
- **Guide d'API.** `docs/api-guide.md` ne décrit que le cœur ; le volet plateforme (invité, essai, chatbots) vit dans `backend/app/plugins/platform/docs/`. `backend/tests/test_api_guide.py` échoue si le guide cite une route absente de l'OpenAPI du cœur seul.
- **Gabarits d'environnement.** L'arbre public garde `.env.example` (gabarit unique) et `.env.prod` (utile à `scripts/init-prod-env.sh`) ; `.env` n'y est pas publié.
- **Licence.** `COMMERCIAL-LICENSE.md` ajoute une permission additionnelle de l'auteur (installer, intégrer, adapter pour un client contre rémunération ; embarquer comme brique interne d'un produit propre) sans toucher au texte de `LICENSE`. Revendre RAGEngine ou l'offrir comme service hébergé payant exige toujours une licence. À relire juridiquement.
