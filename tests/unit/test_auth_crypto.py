from __future__ import annotations

from spoldzielnia_ai.auth.crypto import CsrfTokens, TokenSealer, derive_key


def test_keys_are_separated_by_purpose() -> None:
    assert derive_key("s", "csrf") != derive_key("s", "redis-tokens")
    assert derive_key("s", "csrf") != derive_key("t", "csrf")
    assert len(derive_key("s", "csrf")) == 32


def test_sealed_token_opens_only_in_its_context() -> None:
    sealer = TokenSealer(derive_key("s", "redis-tokens"))
    sealed = sealer.seal("refresh-token", "family-a")
    assert "refresh-token" not in sealed
    assert sealer.seal("refresh-token", "family-a") != sealed  # random nonce
    assert sealer.open(sealed, "family-a") == "refresh-token"
    assert sealer.open(sealed, "family-b") is None
    assert TokenSealer(derive_key("other", "redis-tokens")).open(sealed, "family-a") is None
    assert sealer.open(sealed[:-4] + "AAAA", "family-a") is None
    assert sealer.open("not base64 !!", "family-a") is None


def test_csrf_tokens_are_bound_to_the_session() -> None:
    tokens = CsrfTokens(derive_key("s", "csrf"))
    token = tokens.for_session("sid-a")
    assert tokens.matches("sid-a", token)
    assert not tokens.matches("sid-b", token)
    assert not CsrfTokens(derive_key("t", "csrf")).matches("sid-a", token)
