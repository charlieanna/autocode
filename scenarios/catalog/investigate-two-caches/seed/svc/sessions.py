"""Login sessions. A session created by one worker must be visible to every
other worker, since the load balancer sends a user's requests to any of them."""
import secrets

from . import cache_shared


def create(user: str) -> str:
    token = secrets.token_hex(16)
    cache_shared.put(f"session-{token}", {"user": user})
    return token


def user_for(token: str):
    session = cache_shared.get(f"session-{token}")
    return session["user"] if session else None


def destroy(token: str) -> None:
    cache_shared.delete(f"session-{token}")
