"""Postgres-backed TokenStore (satisfies app.clients.strava.TokenStore).
Refresh tokens encrypted at rest with Fernet — PRD §5.1/§8.1, non-negotiable.
"""
from __future__ import annotations

from app.clients.strava import StravaTokens
from app.security.crypto import TokenCipher
from app.storage.db import Database


class PostgresTokenStore:
    def __init__(self, db: Database, cipher: TokenCipher, provider: str = "strava") -> None:
        self._db = db
        self._cipher = cipher
        self._provider = provider

    def get(self) -> StravaTokens:
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT access_token_enc, refresh_token_enc, expires_at FROM oauth_tokens WHERE provider = %s",
                (self._provider,),
            ).fetchone()
        if row is None:
            raise LookupError(
                f"No stored OAuth tokens for provider={self._provider!r}. Run scripts/bootstrap_oauth.py first."
            )
        access_enc, refresh_enc, expires_at = row
        return StravaTokens(
            access_token=self._cipher.decrypt(access_enc),
            refresh_token=self._cipher.decrypt(refresh_enc),
            expires_at=expires_at,
        )

    def save(self, tokens: StravaTokens) -> None:
        with self._db.connection() as conn:
            conn.execute(
                """
                INSERT INTO oauth_tokens (provider, access_token_enc, refresh_token_enc, expires_at, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (provider) DO UPDATE SET
                    access_token_enc = EXCLUDED.access_token_enc,
                    refresh_token_enc = EXCLUDED.refresh_token_enc,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = now()
                """,
                (self._provider, self._cipher.encrypt(tokens.access_token), self._cipher.encrypt(tokens.refresh_token), tokens.expires_at),
            )
