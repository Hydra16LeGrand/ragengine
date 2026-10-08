# syntax=docker/dockerfile:1
#
# Image du frontend (le plan de déploiement interne, Phase A). Marque : `--build-arg VITE_APP_NAME=...`
# (défaut : RAGEngine, ou celle d'un greffon).
#
# CONTEXTE DE BUILD : ce répertoire (`frontend/`), pas la racine du dépôt — le
# frontend est un paquet npm autonome, rien à copier depuis l'extérieur.
#
# Traefik route vers ce conteneur (provider fichier, cf. docker/traefik/
# dynamic.yml) tout ce qui n'est pas `/api/*` (routé directement vers `api`) :
# ce conteneur ne sert QUE des fichiers statiques, aucun proxy applicatif dedans.

# --------------------------------------------------------------------------- #
# Étape 1 — build Vite (identique à `npm run build` en local, déjà vérifié
# propre cette session : `tsc --noEmit && vite build`).
# --------------------------------------------------------------------------- #
FROM node:22-alpine AS builder

WORKDIR /src

# Couche dépendances séparée du code source : un changement de code seul ne
# réinvalide pas `npm ci` au rebuild suivant.
#
# --legacy-peer-deps : conflit de peer dependency PRÉEXISTANT (pas introduit
# ici, reproduit hors Docker par un `npm ci` local) — `typescript@^6.0.3`
# (package.json) contre le peer `typescript@^5.x` exigé par
# `openapi-typescript@7.13.0`. `npm install` incrémental le tolère localement
# (le `node_modules` existant ne repasse pas par une résolution stricte), mais
# `npm ci` (résolution fraîche à chaque fois, seule option fiable en image
# Docker) l'échoue net. Ne règle pas le désaccord de version — juste ce qu'il
# faut pour que le build reproduise ce qui tourne déjà localement. À
# trancher séparément (retour à typescript 5.x, ou mise à jour
# d'openapi-typescript) : hors du périmètre Traefik/conteneurisation de cette
# livraison (le plan de déploiement interne, Phase A).
COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps

COPY . .
# Marque optionnelle : vide, la SPA retombe sur celle du greffon, sinon sur RAGEngine.
ARG VITE_APP_NAME=
ENV VITE_APP_NAME=$VITE_APP_NAME
RUN npm run build

# --------------------------------------------------------------------------- #
# Étape 2 — serveur statique minimal derrière Traefik. Variante "unprivileged"
# : écoute en 8080 et tourne déjà en non-root, même politique que
# `backend/Dockerfile` (utilisateur applicatif dédié, jamais root).
# --------------------------------------------------------------------------- #
FROM nginxinc/nginx-unprivileged:1.31-alpine AS runtime

COPY --from=builder /src/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
