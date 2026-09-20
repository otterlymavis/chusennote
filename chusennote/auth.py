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


def valid_email(email: str) -> bool:
    if not email or len(email) > MAX_EMAIL_LENGTH or email.count("@") != 1 or any(char.isspace() for char in email):
        return False
    local, domain = email.split("@", 1)
    return bool(local and domain)


def user_from_row(row: object) -> User:
    return User(id=int(row[0]), email=str(row[1]), created_at=str(row[2]))


def create_user(db_path: str, email: str, password: str, now: str | None = None) -> User:
    email = normalize_email(email)
    if not valid_email(email):
        raise ValueError("a valid email is required")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"password must be {MAX_PASSWORD_LENGTH} characters or fewer")
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
    valid_input = valid_email(email) and len(password) <= MAX_PASSWORD_LENGTH
    row = None
    if valid_input:
        with connect(db_path) as connection:
            init_db(connection)
            row = connection.execute(
                "SELECT id, email, created_at, password_hash, password_salt FROM users WHERE email = ?",
                (email,),
            ).fetchone()
    # Always run one bounded pbkdf2 comparison. The fixed dummy record keeps an
    # unknown/invalid account close to the wrong-password work factor without
    # doing an extra hash or processing an attacker-controlled oversized value.
    if row:
        password_hash, salt = str(row[3]), str(row[4])
    else:
        password_hash, salt = "0" * 64, "0" * 32
    candidate_password = password if len(password) <= MAX_PASSWORD_LENGTH else "invalid oversized password"
    matches = password_matches(candidate_password, password_hash, salt)
    if not valid_input or not row or not matches:
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
    if not token or len(token) > MAX_AUTH_TOKEN_LENGTH:
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


def revoke_token(db_path: str, token: str, device_token: str = "") -> bool:
    if not token or len(token) > MAX_AUTH_TOKEN_LENGTH or len(device_token) > MAX_DEVICE_TOKEN_LENGTH:
        return False
    with connect(db_path) as connection:
        init_db(connection)
        # Detach only this session's device, never another account's or the
        # user's other devices. Keep detachment and revocation atomic.
        if device_token:
            owner = connection.execute(
                "SELECT user_id FROM api_tokens WHERE token_hash = ?", (token_fingerprint(token),)
            ).fetchone()
            if owner is None and connection.execute(
                "SELECT 1 FROM device_tokens WHERE token = ? AND user_id > 0", (device_token,)
            ).fetchone():
                return False
            # A retry after a lost logout response is safe once this device is
            # no longer registered to an account, even though auth was revoked.
            connection.execute(
                """
                DELETE FROM device_tokens
                WHERE token = ? AND user_id = (
                    SELECT user_id FROM api_tokens WHERE token_hash = ?
                )
                """,
                (device_token, token_fingerprint(token)),
            )
        connection.execute(
            "DELETE FROM api_tokens WHERE token_hash = ?", (token_fingerprint(token),)
        )
    return True


def issue_calendar_token(
    db_path: str, user_id: int, now: str | None = None, *, rotate: bool = False
) -> str:
    """Mint a calendar-only token, preserving other devices' subscriptions.

    Kept in its own table (never consulted by ``user_for_token``) so it can be
    embedded in a shareable ``/calendar.ics?token=...`` URL without handing out
    full API access the way the regular bearer token would. Explicit rotation
    revokes the user's existing calendar URLs; ordinary issuance never does.
    """
    token = generate_token()
    timestamp = now or utc_now_iso()
    with connect(db_path) as connection:
        init_db(connection)
        if rotate:
            connection.execute("DELETE FROM calendar_tokens WHERE user_id = ?", (user_id,))
        connection.execute(
            "INSERT INTO calendar_tokens(user_id, token_hash, created_at) VALUES (?, ?, ?)",
            (user_id, token_fingerprint(token), timestamp),
        )
    return token


def user_id_for_calendar_token(db_path: str, token: str | None) -> int | None:
    if not token or len(token) > MAX_AUTH_TOKEN_LENGTH:
        return None
    fingerprint = token_fingerprint(token)
    with connect(db_path) as connection:
        init_db(connection)
        row = connection.execute(
            "SELECT user_id FROM calendar_tokens WHERE token_hash = ?", (fingerprint,)
        ).fetchone()
    return int(row[0]) if row else None
