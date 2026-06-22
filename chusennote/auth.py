"""User accounts and bearer-token authentication for chusennote.

Email/password accounts with opaque bearer tokens, built on the Python standard
library (pbkdf2 + secrets) so the core stays dependency-free and works on both
SQLite and Postgres through the storage seam. Passwords are stored as salted
pbkdf2 hashes; tokens are random and stored only as SHA-256 fingerprints, so a
database leak never exposes a usable credential.

Other login providers (e.g. Apple/Google sign-in) can be layered on later: they
would resolve to a User and mint a token via ``issue_token`` here, reusing the
same accounts and token validation.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from .models import *  # noqa: F401,F403
from .schema import *  # noqa: F401,F403  (connect, init_db, utc_now_iso, clean_text)

PBKDF2_ROUNDS = 200_000
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ROUNDS
    ).hex()
    return digest, salt


def password_matches(password: str, password_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    return clean_text(email).lower()


def user_from_row(row: object) -> User:
    return User(id=int(row[0]), email=str(row[1]), created_at=str(row[2]))


def create_user(db_path: str, email: str, password: str, now: str | None = None) -> User:
    email = normalize_email(email)
    if not email or "@" not in email:
        raise ValueError("a valid email is required")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    timestamp = now or utc_now_iso()
    password_hash, salt = hash_password(password)
    with connect(db_path) as connection:
        init_db(connection)
        if connection.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            raise ValueError("email already registered")
        connection.execute(
            """
            INSERT INTO users(email, password_hash, password_salt, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (email, password_hash, salt, timestamp, timestamp),
        )
        row = connection.execute(
            "SELECT id, email, created_at FROM users WHERE email = ?", (email,)
        ).fetchone()
    return user_from_row(row)


def verify_user(db_path: str, email: str, password: str) -> User | None:
    email = normalize_email(email)
    with connect(db_path) as connection:
        init_db(connection)
        row = connection.execute(
            "SELECT id, email, created_at, password_hash, password_salt FROM users WHERE email = ?",
            (email,),
        ).fetchone()
    if not row or not password_matches(password, str(row[3]), str(row[4])):
        return None
    return user_from_row(row)


def issue_token(db_path: str, user_id: int, now: str | None = None) -> str:
    """Mint a new bearer token for a user and return the plaintext (shown once)."""
    token = generate_token()
    timestamp = now or utc_now_iso()
    with connect(db_path) as connection:
        init_db(connection)
        connection.execute(
            "INSERT INTO api_tokens(user_id, token_hash, created_at) VALUES (?, ?, ?)",
            (user_id, token_fingerprint(token), timestamp),
        )
    return token


def user_for_token(db_path: str, token: str | None, now: str | None = None) -> User | None:
    if not token:
        return None
    fingerprint = token_fingerprint(token)
    timestamp = now or utc_now_iso()
    with connect(db_path) as connection:
        init_db(connection)
        row = connection.execute(
            """
            SELECT u.id, u.email, u.created_at
            FROM api_tokens t JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = ?
            """,
            (fingerprint,),
        ).fetchone()
        if not row:
            return None
        connection.execute(
            "UPDATE api_tokens SET last_used_at = ? WHERE token_hash = ?",
            (timestamp, fingerprint),
        )
    return user_from_row(row)


def revoke_token(db_path: str, token: str) -> None:
    with connect(db_path) as connection:
        init_db(connection)
        connection.execute(
            "DELETE FROM api_tokens WHERE token_hash = ?", (token_fingerprint(token),)
        )
