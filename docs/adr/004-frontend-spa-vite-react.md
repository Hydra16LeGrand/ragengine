# ADR-004 — Frontend : SPA Vite + React + TypeScript

**Date :** 2026-07-25 · **Statut :** accepté

## Contexte
Déploiement souverain local, pas de besoin SEO ni de rendu serveur ; l'UI est une application riche (dashboard, flux d'ingestion temps réel, playground).

## Décision
SPA **Vite + React + TypeScript strict**. Pas de Next.js. État serveur via TanStack Query (polling des jobs), client API typé généré depuis l'OpenAPI du backend, design system maison dans `src/design/` (cf. CONTRIBUTING.md §7).

## Conséquences
- Build et dev-server simples, servibles par un conteneur statique derrière la façade.
- L'auth UI (ADR-005) se fait côté SPA (sessions/tokens contre l'API), pas de middleware serveur de rendu.
