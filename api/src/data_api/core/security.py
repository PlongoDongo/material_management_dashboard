"""
Authentication -- deliberately minimal, but wired in at the right place.

Right now: an optional API key in the `X-API-Key` header. If `API_KEYS` is
empty, the check is disabled (development).

The point of this module is not the API key but the *shape*: authentication is
a FastAPI dependency that yields a `Principal`. Switching to OIDC/JWT (Azure AD
or similar) only replaces the implementation of `current_principal` -- no router
and no data product has to change. Later row-level authorisation hangs here too:
`Principal.groups` comes from the token, and a data product can declare
`required_groups`.
"""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from data_api.core.config import Settings, get_settings


@dataclass(frozen=True)
class Principal:
    """Who is asking? Later this comes from the OIDC token instead of an API key."""

    subject: str
    groups: frozenset[str] = field(default_factory=frozenset)
    auth_enabled: bool = True

    def may_access(self, required_groups: Iterable[str]) -> bool:
        """May this caller see a product that declares `required_groups`?

        When authentication is switched off (development), everything is open.
        Without that line development would be *stricter* than production: the
        anonymous caller only has the group "public" and would get a 403 on a
        product with required_groups=("internal",) -- even though auth is off.
        """
        if not self.auth_enabled:
            return True
        return not required_groups or bool(self.groups.intersection(required_groups))


ANONYMOUS = Principal(subject="anonymous", groups=frozenset({"public"}), auth_enabled=False)


async def current_principal(
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Principal:
    if not settings.auth_enabled:
        return ANONYMOUS

    # `compare_digest` instead of `in`: the comparison runs in constant time and
    # does not leak, via response timing, how many characters matched.
    if not any(secrets.compare_digest(x_api_key or "", known) for known in settings.api_keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid X-API-Key is required.",
        )

    # This subject ends up in logs and in responses (MappingOut.changed_by), so
    # it is a hash rather than characters of the key itself: equally usable for
    # telling keys apart, but not reversible.
    fingerprint = hashlib.sha256(x_api_key.encode()).hexdigest()[:8]
    return Principal(subject=f"apikey:{fingerprint}", groups=frozenset({"internal"}))


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
