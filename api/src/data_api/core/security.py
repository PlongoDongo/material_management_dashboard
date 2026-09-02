"""
Authentifizierung -- bewusst minimal, aber an der richtigen Stelle verdrahtet.

Aktuell: optionaler API-Key im Header `X-API-Key`. Ist `API_KEYS` leer, ist die
Pruefung aus (Entwicklung).

Der Punkt dieses Moduls ist nicht der API-Key, sondern die *Form*: Auth ist eine
FastAPI-Dependency. Der Wechsel auf OIDC/JWT (Azure AD o. ae.) tauscht nur die
Implementierung von `current_principal` aus -- kein Router und kein Datenprodukt
muss angefasst werden. Genauso haengt spaeteres RBAC hier: `Principal.groups`
kommt aus dem Token, und ein Datenprodukt kann `required_groups` deklarieren.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from data_api.core.config import Settings, get_settings


@dataclass(frozen=True)
class Principal:
    """Wer fragt? Spaeter aus dem OIDC-Token statt aus dem API-Key."""

    subject: str
    groups: frozenset[str] = field(default_factory=frozenset)

    def has_any(self, groups) -> bool:
        """Leere Anforderung = fuer alle offen. Sonst muss eine Gruppe passen."""
        return not groups or bool(self.groups.intersection(groups))


ANONYMOUS = Principal(subject="anonymous", groups=frozenset({"public"}))


async def current_principal(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Principal:
    if not settings.auth_enabled:
        return ANONYMOUS
    if x_api_key not in settings.api_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Gueltiger X-API-Key erforderlich.",
        )
    return Principal(subject=f"apikey:{x_api_key[:4]}...", groups=frozenset({"internal"}))


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
