#!/usr/bin/env bash
#
# Garde-fou de dérive du contrat d'API — le client frontend contre le serveur vivant.
#
#   scripts/check-api-drift.sh        # régénère schema.ts et échoue si un diff apparaît
#   scripts/check-api-drift.sh --fix  # réécrit schema.ts depuis le serveur, à relire ensuite
#
# Le client typé (`frontend/src/api/schema.ts`) est généré depuis l'OpenAPI du
# backend (ADR-007 : le contrat d'API est une source de vérité versionnée). Si
# le serveur vivant ne produit plus le même schéma, c'est une dérive — soit le
# backend a changé sans régénérer le client, soit le frontend a été modifié à
# la main. Le faire échouer en CI plutôt que de laisser le contrat dériver.
#
# Exige la stack déployée et `openapi-typescript` installé dans `frontend/`.
set -euo pipefail

cd "$(dirname "$0")/../frontend"

API_URL="${VITE_OPENAPI_URL:-http://127.0.0.1:8000/api/v1/openapi.json}"
OUT="src/api/schema.ts"

if [[ "${1:-}" == "--fix" ]]; then
    npx openapi-typescript "$API_URL" -o "$OUT"
    echo "schema.ts régénéré — le relire (git diff frontend/src/api/schema.ts) avant de committer."
    exit 0
fi

cp "$OUT" /tmp/schema.ts.before
npx openapi-typescript "$API_URL" -o "$OUT"

if diff -q /tmp/schema.ts.before "$OUT" >/dev/null; then
    echo "OK : aucun écart entre le contrat d'API vivant et frontend/src/api/schema.ts."
    exit 0
fi

echo "DÉRIVE DU CONTRAT D'API : le serveur vivant ne produit plus le schéma committé." >&2
echo "Revoir le diff (git diff frontend/src/api/schema.ts), régénérer délibérément" >&2
echo "(scripts/check-api-drift.sh --fix), puis relire avant de committer." >&2
git diff --stat -- "$OUT" >&2
exit 1
