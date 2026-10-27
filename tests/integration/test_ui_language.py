"""Chunk 14.1: the UI language on the account (US-08). ``/me`` and ``PUT /me/locale``, the
``lang`` cookie set at sign-in from the account or adopted from a signed-out visitor, and the
database check of migration 0028."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from spoldzielnia_ai.db.models import User
from spoldzielnia_ai.db.session import SessionFactory, create_session_factory
from tests.fixtures.app import HOST, SITE, Harness, harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from httpx import Response

    from spoldzielnia_ai.core.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def factory(fresh_db: str) -> AsyncIterator[SessionFactory]:
    engine = create_async_engine(fresh_db)
    yield create_session_factory(engine)
    await engine.dispose()


async def _sign_in(h: Harness, client: AsyncClient, subject: str) -> Response:
    """The OIDC round trip on ``client`` (which may already carry a ``lang`` cookie)."""
    start = await client.get("/auth/login")
    params = parse_qs(urlsplit(start.headers["location"]).query)
    h.idp.id_token = h.idp.make_id_token(nonce=params["nonce"][0], sub=subject)
    callback = await client.get("/auth/callback", params={"state": params["state"][0], "code": "c"})
    assert callback.status_code == 303, callback.text
    return callback


def _lang_cookies(response: Response) -> list[str]:
    return [c for c in response.headers.get_list("set-cookie") if c.startswith("lang=")]


async def _locale(factory: SessionFactory, subject: str) -> str | None:
    async with factory() as session:
        return await session.scalar(select(User.locale).where(User.oidc_subject == subject))


async def test_polish_until_chosen_then_english_everywhere(
    settings: Settings, factory: SessionFactory
) -> None:
    async with harness(settings, factory) as h:
        member = await h.login()
        me = (await member.client.get("/me")).json()
        assert (me["locale"], me["locale_chosen"]) == ("pl", False)

        response = await member.client.put(
            "/me/locale", json={"locale": "en"}, headers=member.headers
        )
        assert response.status_code == 204
        cookie = _lang_cookies(response)
        assert len(cookie) == 1
        assert cookie[0].startswith("lang=en;")
        me = (await member.client.get("/me")).json()
        assert (me["locale"], me["locale_chosen"]) == ("en", True)


@pytest.mark.parametrize("value", ["de", "EN", "en-US", "", None, 1])
async def test_only_the_two_codes_are_accepted(
    settings: Settings, factory: SessionFactory, value: object
) -> None:
    async with harness(settings, factory) as h:
        member = await h.login()
        response = await member.client.put(
            "/me/locale", json={"locale": value}, headers=member.headers
        )
        assert response.status_code == 422
        assert _lang_cookies(response) == []


async def test_a_write_needs_csrf_and_a_session(
    settings: Settings, factory: SessionFactory
) -> None:
    async with harness(settings, factory) as h:
        member = await h.login()
        no_csrf = await member.client.put(
            "/me/locale", json={"locale": "en"}, headers={"Origin": SITE}
        )
        assert no_csrf.status_code == 403
        async with AsyncClient(transport=ASGITransport(app=h.app), base_url=SITE) as anonymous:
            response = await anonymous.put(
                "/me/locale", json={"locale": "en"}, headers={"Origin": SITE}
            )
        assert response.status_code in (401, 403)
        assert (await member.client.get("/me")).json()["locale_chosen"] is False


async def test_changes_are_rate_limited(settings: Settings, factory: SessionFactory) -> None:
    async with harness(settings, factory) as h:
        member = await h.login()
        for i in range(30):
            locale = ("pl", "en")[i % 2]
            response = await member.client.put(
                "/me/locale", json={"locale": locale}, headers=member.headers
            )
            assert response.status_code == 204
        response = await member.client.put(
            "/me/locale", json={"locale": "en"}, headers=member.headers
        )
        assert response.status_code == 429
        assert response.json()["detail"] == {"error": "rate_limited"}


async def test_sign_in_sets_the_cookie_from_the_account(
    settings: Settings, factory: SessionFactory
) -> None:
    """Another device is in the account's language from its first page."""
    subject = secrets.token_hex(8)
    async with harness(settings, factory) as h:
        first = AsyncClient(transport=ASGITransport(app=h.app), base_url=SITE)
        h.clients.append(first)
        callback = await _sign_in(h, first, subject)
        assert _lang_cookies(callback) == []  # nothing chosen: no cookie, Polish
        csrf = first.cookies.get("__Host-csrf", domain=HOST) or ""
        await first.put(
            "/me/locale", json={"locale": "en"}, headers={"Origin": SITE, "X-CSRF-Token": csrf}
        )

        second = AsyncClient(transport=ASGITransport(app=h.app), base_url=SITE)
        h.clients.append(second)
        second.cookies.set("lang", "pl", domain=HOST)  # a stale cookie on that device
        callback = await _sign_in(h, second, subject)
        assert [c.split(";")[0] for c in _lang_cookies(callback)] == ["lang=en"]
        assert await _locale(factory, subject) == "en"  # the account wins over the cookie


async def test_a_visitors_choice_is_adopted_by_an_account_without_one(
    settings: Settings, factory: SessionFactory
) -> None:
    subject = secrets.token_hex(8)
    async with harness(settings, factory) as h:
        client = AsyncClient(transport=ASGITransport(app=h.app), base_url=SITE)
        h.clients.append(client)
        client.cookies.set("lang", "en", domain=HOST)
        callback = await _sign_in(h, client, subject)
        assert await _locale(factory, subject) == "en"
        assert [c.split(";")[0] for c in _lang_cookies(callback)] == ["lang=en"]
        assert (await client.get("/me")).json()["locale_chosen"] is True


@pytest.mark.parametrize("value", ["de", "EN", "en-GB", "<script>"])
async def test_a_forged_cookie_is_ignored_at_sign_in(
    settings: Settings, factory: SessionFactory, value: str
) -> None:
    subject = secrets.token_hex(8)
    async with harness(settings, factory) as h:
        client = AsyncClient(transport=ASGITransport(app=h.app), base_url=SITE)
        h.clients.append(client)
        client.cookies.set("lang", value, domain=HOST)
        callback = await _sign_in(h, client, subject)
        assert await _locale(factory, subject) is None
        assert _lang_cookies(callback) == []


async def test_the_database_refuses_another_locale(factory: SessionFactory) -> None:
    async with factory() as session:
        with pytest.raises(IntegrityError, match="ck_app_user_locale"):
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO app_user (id, oidc_issuer, oidc_subject, locale) "
                        "VALUES (gen_random_uuid(), 'i', 's', 'de')"
                    )
                )
