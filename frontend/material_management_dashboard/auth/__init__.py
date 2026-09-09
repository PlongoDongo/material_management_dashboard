"""Anmeldung des Dashboards gegen Keycloak. Siehe auth/keycloak.py."""
from auth.keycloak import (
    access_token,
    auth_enabled,
    register_auth,
    user_roles,
    username,
)

__all__ = ["access_token", "auth_enabled", "register_auth", "user_roles", "username"]
