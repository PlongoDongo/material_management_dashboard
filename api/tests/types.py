"""
Type aliases for the fixtures in conftest.py.

Their own module so the aliases can be imported without pulling in conftest --
importing a conftest by hand works, but it is the kind of thing that stops
working the day someone moves a fixture.

The aliases exist so a signature like

    def test_x(client: TestClient, auth_header: AuthHeader) -> None:

stays readable. `Callable[..., dict[str, str]]` spelled out at all seventeen
call sites would be noise, not information.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

# `auth_header(roles=["planner"], username="x")` -> {"Authorization": "Bearer ..."}
AuthHeader = Callable[..., dict[str, str]]

# `make_token(roles=[...], expires_in=-60)` -> the encoded JWT
MakeToken = Callable[..., str]

# (private key, public key) from `cryptography`. Deliberately not the concrete
# RSAPrivateKey/RSAPublicKey classes: that would make every test file import
# `cryptography.hazmat`, a transitive dependency of pyjwt, for no gain.
KeyPair = tuple[Any, Any]
