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

import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from data_api.core.config import Settings, get_settings


@dataclass(frozen=True)
class Principal:
    """Wer fragt? Spaeter aus dem OIDC-Token statt aus dem API-Key."""

    subject: str
    groups: frozenset[str] = field(default_factory=frozenset)
    auth_aktiv: bool = True

    def darf(self, benoetigt) -> bool:
        """Darf dieser Aufrufer ein Produkt sehen, das `benoetigt` verlangt?

        Ist die Authentifizierung ausgeschaltet (Entwicklung), ist ALLES offen.
        Ohne diese Zeile waere die Entwicklung strenger als die Produktion: der
        anonyme Aufrufer haette nur die Gruppe "public" und bekaeme bei einem
        Produkt mit required_groups=("internal",) ein 403 -- obwohl Auth aus ist.
        """
        if not self.auth_aktiv:
            return True
        return not benoetigt or bool(self.groups.intersection(benoetigt))


ANONYMOUS = Principal(subject="anonymous", groups=frozenset({"public"}), auth_aktiv=False)


async def current_principal(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Principal:
    if not settings.auth_enabled:
        return ANONYMOUS
    # `compare_digest` statt `in`: der Vergleich laeuft in konstanter Zeit und
    # verraet ueber die Antwortdauer nicht, wie viele Zeichen gestimmt haben.
    if not any(secrets.compare_digest(x_api_key or "", bekannt) for bekannt in settings.api_keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Gueltiger X-API-Key erforderlich.",
        )
    # Der Bezeichner landet in Logs und in Antworten (MappingOut.geaendert_von).
    # Deshalb ein Hash und keine Zeichen des Schluessels selbst: gleich gut zum
    # Unterscheiden, aber nicht rueckrechenbar.
    kennung = hashlib.sha256(x_api_key.encode()).hexdigest()[:8]
    return Principal(subject=f"apikey:{kennung}", groups=frozenset({"internal"}))


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
