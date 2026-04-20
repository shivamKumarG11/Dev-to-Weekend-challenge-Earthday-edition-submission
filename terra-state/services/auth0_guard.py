"""
services/auth0_guard.py — TERRA-STATE: VOX ATLAS v2.0
Auth0 JWKS JWT verification — FastAPI dependency.

Mock path: AUTH0_DOMAIN is unset → accepts 'dev-token' (local dev).
Live path:  RS256 JWKS verification via PyJWT.
"""
from __future__ import annotations
import json
import logging
import os
import time
from typing import Optional

import httpx
from fastapi import HTTPException, Request, status

log = logging.getLogger("terra-state")

AUTH0_DOMAIN   = os.getenv("AUTH0_DOMAIN",   "")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "")

_jwks_cache:        dict  = {}
_jwks_fetched_at:   float = 0.0
_JWKS_TTL_SECONDS:  float = 300.0   # Re-fetch JWKS every 5 minutes


async def _fetch_and_cache_jwks() -> dict:
    """Fetch Auth0 JWKS, caching with a 5-minute TTL."""
    global _jwks_cache, _jwks_fetched_at
    now = time.monotonic()
    if _jwks_cache and (now - _jwks_fetched_at) < _JWKS_TTL_SECONDS:
        return _jwks_cache

    url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            _jwks_cache      = resp.json()
            _jwks_fetched_at = now
            log.info("JWKS cache refreshed from Auth0.")
            return _jwks_cache
    except Exception as exc:
        log.error(f"Auth0 JWKS fetch failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Auth0 JWKS endpoint unreachable: {exc}",
        )


async def verify_agent_token(request: Request) -> dict:
    """
    FastAPI Depends — verifies the Bearer JWT on /agent-request.

    Returns the decoded JWT payload dict on success.
    Raises 401/503 on failure.
    """
    auth_header: Optional[str] = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header. Use 'Bearer <token>'.",
        )
    token = auth_header.split(" ", 1)[1].strip()

    # ── Mock path: no AUTH0_DOMAIN configured ─────────────────────────────────
    if not AUTH0_DOMAIN:
        if token == "dev-token":
            log.warning("Auth0 MOCK — accepted dev-token.")
            return {"sub": "mock-agent-001", "mock": True}
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="AUTH0_DOMAIN not configured. Use 'dev-token' for local development.",
        )

    # ── Live path: RS256 JWKS verification ────────────────────────────────────
    try:
        import jwt
        from jwt.algorithms import RSAAlgorithm

        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        jwks = await _fetch_and_cache_jwks()

        rsa_key = None
        for key_data in jwks.get("keys", []):
            if key_data.get("kid") == kid:
                rsa_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
                break

        if rsa_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"No public key found in JWKS for kid='{kid}'.",
            )

        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            audience=AUTH0_AUDIENCE,
            issuer=f"https://{AUTH0_DOMAIN}/",
        )
        return payload

    except HTTPException:
        raise
    except Exception as exc:
        log.warning(f"JWT verification failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {exc}",
        )
