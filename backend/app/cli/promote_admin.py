"""Promeut un compte réel au rôle plateforme `admin` (ADR-009/ADR-048).

Comble un trou d'amorçage réel : aucun parcours d'inscription ni aucune route
API ne peut créer le premier admin — attribuer ce rôle exige déjà de l'avoir.
L'administrateur plateforme voit et gère toute base et toute organisation
(`PermissionService`). Un premier admin ne peut donc naître que d'un accès
direct à la base ; cette
commande le fait proprement (via les modèles applicatifs, pas du SQL à la
main) plutôt que de laisser cet accès direct être la seule voie documentée.

Usage (depuis le conteneur `api`, jamais depuis l'hôte — cf. docstring du
paquet) :

    docker exec ragengine-api python -m app.cli.promote_admin quelqu'un@exemple.com

Idempotent : promouvoir un compte déjà `admin` ne fait rien de plus qu'une
confirmation.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models.user import User
from app.db.session import create_engine
from app.domain.enums import UserRole

_USAGE = "Usage : python -m app.cli.promote_admin <email>"


async def promote_admin(email: str) -> User:
    """Résout le compte par e-mail (insensible à la casse) et fixe `role=admin`.

    Raises:
        LookupError: aucun compte réel (non invité) ne porte cette adresse.
    """
    settings = get_settings()
    engine = create_engine(settings)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            statement = select(User).where(func.lower(User.email) == email.strip().lower())
            user = (await session.execute(statement)).scalar_one_or_none()
            if user is None:
                raise LookupError(f"Aucun compte réel ne porte l'adresse « {email} ».")
            user.role = UserRole.ADMIN
            await session.commit()
            await session.refresh(user)
            return user
    finally:
        await engine.dispose()


def main() -> None:
    if len(sys.argv) != 2:
        print(_USAGE, file=sys.stderr)
        raise SystemExit(2)

    email = sys.argv[1]
    try:
        user = asyncio.run(promote_admin(email))
    except LookupError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error

    print(f"« {user.email} » est désormais admin plateforme (id={user.id}).")


if __name__ == "__main__":
    main()
