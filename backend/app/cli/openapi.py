"""Imprime le schéma OpenAPI de l'application, sans serveur ni base de données.

Source des types générés du frontend (`npm run generate:api`) :

    python -m app.cli.openapi --core > openapi.json   # cœur seul (frontend/src/api/schema.ts)
    python -m app.cli.openapi > openapi.json          # cœur et greffons présents

`--core` construit l'application sans aucun greffon (`plugins_overridden(())`,
ADR-069) : c'est le contrat du cœur, celui que consomme `src/api/` — les types
d'un greffon se génèrent à part, depuis le schéma complet. Sans greffon
installé, les deux sorties sont identiques.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from app.core.plugins import plugins_overridden


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--core", action="store_true", help="contrat du cœur seul, sans greffon")
    args = parser.parse_args(argv)
    # Avant l'import d'`app.main`, qui construit l'application et journalise sur la
    # sortie standard : seule la sortie JSON doit y paraître.
    logging.disable(logging.CRITICAL)
    from app.main import create_app

    if args.core:
        with plugins_overridden(()):
            schema = create_app().openapi()
    else:
        schema = create_app().openapi()
    json.dump(schema, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
