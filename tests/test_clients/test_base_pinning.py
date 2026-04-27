"""Characterisation tests for the shared ArrClient base behaviour.

Track A.5 of the refactor plan.  The Track C refactor will introduce a
``_fetch_wanted_page`` template method on ``ArrClient`` and the per-app
subclasses will switch to class-level hook attributes.  These pinning
tests lock the pre-refactor contract of ``_get``, ``_post``, ``ping``,
``__aenter__``, ``__aexit__``, ``aclose``, and the constructor side-effects
(base-URL normalisation, header injection, default timeout) so the
template extraction cannot silently drift their observable behaviour.

We drive the contract via a minimal concrete subclass that satisfies the
abstract interface without adding any behaviour of its own; the existing
SonarrClient / RadarrClient tests exercise the concrete overrides.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from houndarr.clients.base import ArrClient, WantedKind

pytestmark = pytest.mark.pinning


class _StubClient(ArrClient):
    """Minimal concrete ArrClient that returns canned values for abstract methods.

    The stub never issues real HTTP calls except through the base-class
    helpers we are pinning.  get_wanted_total returns a configurable int
    so the default get_instance_snapshot can be verified.
    """

    wanted_totals: dict[WantedKind, int]

    def __init__(
        self,
        url: str,
        api_key: str,
        *,
        wanted_totals: dict[WantedKind, int] | None = None,
    ) -> None:
        super().__init__(url=url, api_key=api_key)
        self.wanted_totals = wanted_totals or {"missing": 0, "cutoff": 0}

    async def get_missing(self, *, page: int = 1, page_size: int = 10) -> list[Any]:
        return []

    async def get_cutoff_unmet(self, *, page: int = 1, page_size: int = 10) -> list[Any]:
        return []

    async def search(self, item_id: int) -> None:
        return None

    async def get_wanted_total(self, kind: WantedKind) -> int:
        return self.wanted_totals[kind]


# Constructor side-effects


class TestConstructor:
    """Pin base-URL normalisation, header injection, and timeout defaults."""

    def test_trailing_slash_stripped_from_base_url(self) -> None:
        """Trailing slashes on the URL argument are stripped at construction."""
        stub = _StubClient(url="http://sonarr:8989/", api_key="k")
        assert stub._client.base_url == httpx.URL("http://sonarr:8989")

    def test_multiple_trailing_slashes_only_one_stripped(self) -> None:
        """``rstrip("/")`` is greedy; every trailing slash is stripped."""
        stub = _StubClient(url="http://sonarr:8989///", api_key="k")
        assert stub._client.base_url == httpx.URL("http://sonarr:8989")

    def test_api_key_header_set(self) -> None:
        """``X-Api-Key`` is populated from the api_key argument."""
        stub = _StubClient(url="http://sonarr:8989", api_key="secret-k")
        assert stub._client.headers["X-Api-Key"] == "secret-k"

    def test_accept_header_set(self) -> None:
        """``Accept`` is hard-coded to ``application/json``."""
        stub = _StubClient(url="http://sonarr:8989", api_key="k")
        assert stub._client.headers["Accept"] == "application/json"

    def test_default_timeout_is_30_read_5_connect(self) -> None:
        """Pin the default timeout shape (read=30s, connect=5s)."""
        stub = _StubClient(url="http://sonarr:8989", api_key="k")
        timeout = stub._client.timeout
        assert timeout.read == 30.0
        assert timeout.connect == 5.0

    def test_arrclient_follow_redirects_is_false_by_default(self) -> None:
        """The client must never follow redirects automatically.

        SSRF posture: an ``*arr`` response that redirects to a blocked
        target (loopback, link-local, metadata service) must reach the
        caller as a 3xx, not as a followed 200 from the blocked host.
        httpx's own default is ``False``; Phase 3a makes the kwarg
        explicit at `base.py:99`.  This pin stays green before and
        after the explicit flip so a future httpx upgrade that flips
        the default cannot silently weaken the posture.
        """
        stub = _StubClient(url="http://sonarr:8989", api_key="k")
        assert stub._client.follow_redirects is False


# _get + _post


class TestGetPost:
    """Pin the low-level HTTP helpers' success and failure semantics."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_returns_parsed_json(self) -> None:
        """_get returns the decoded JSON body on 2xx."""
        respx.get("http://sonarr:8989/ping").mock(
            return_value=httpx.Response(200, json={"hello": "world"}),
        )
        async with _StubClient(url="http://sonarr:8989", api_key="k") as client:
            result = await client._get("/ping")
        assert result == {"hello": "world"}

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_forwards_query_params(self) -> None:
        """_get forwards keyword args as query parameters."""
        route = respx.get("http://sonarr:8989/ping").mock(
            return_value=httpx.Response(200, json={"ok": True}),
        )
        async with _StubClient(url="http://sonarr:8989", api_key="k") as client:
            await client._get("/ping", page=2, pageSize=10, monitored="true")
        req = route.calls[0].request
        assert req.url.params.get("page") == "2"
        assert req.url.params.get("pageSize") == "10"
        assert req.url.params.get("monitored") == "true"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_get_raises_on_4xx(self) -> None:
        """``raise_for_status`` fires for every non-2xx."""
        respx.get("http://sonarr:8989/ping").mock(return_value=httpx.Response(404))
        async with _StubClient(url="http://sonarr:8989", api_key="k") as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client._get("/ping")

    @pytest.mark.asyncio()
    @respx.mock
    async def test_post_sends_json_body(self) -> None:
        """_post forwards the json kwarg as the request body."""
        import json as _json

        route = respx.post("http://sonarr:8989/cmd").mock(
            return_value=httpx.Response(200, json={"id": 1}),
        )
        async with _StubClient(url="http://sonarr:8989", api_key="k") as client:
            await client._post("/cmd", json={"name": "Ping"})
        body = _json.loads(route.calls[0].request.content)
        assert body == {"name": "Ping"}

    @pytest.mark.asyncio()
    @respx.mock
    async def test_post_raises_on_5xx(self) -> None:
        """``raise_for_status`` fires for 5xx as well as 4xx."""
        respx.post("http://sonarr:8989/cmd").mock(return_value=httpx.Response(500))
        async with _StubClient(url="http://sonarr:8989", api_key="k") as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client._post("/cmd", json={})


# ping: swallow-all contract


class TestPing:
    """Pin that ping collapses every failure mode to None and parses success."""

    @pytest.mark.asyncio()
    @respx.mock
    async def test_ping_returns_system_status_on_2xx(self) -> None:
        """ping returns the parsed SystemStatus on success."""
        respx.get("http://sonarr:8989/api/v3/system/status").mock(
            return_value=httpx.Response(200, json={"appName": "Sonarr", "version": "4.0.0"}),
        )
        async with _StubClient(url="http://sonarr:8989", api_key="k") as client:
            status = await client.ping()
        assert status is not None
        assert status.app_name == "Sonarr"
        assert status.version == "4.0.0"

    @pytest.mark.asyncio()
    @respx.mock
    async def test_ping_returns_none_on_http_500(self) -> None:
        """A 5xx collapses to None (httpx.HTTPError swallowed)."""
        respx.get("http://sonarr:8989/api/v3/system/status").mock(
            return_value=httpx.Response(500),
        )
        async with _StubClient(url="http://sonarr:8989", api_key="k") as client:
            assert await client.ping() is None

    @pytest.mark.asyncio()
    @respx.mock
    async def test_ping_returns_none_on_connect_error(self) -> None:
        """A transport error collapses to None."""
        respx.get("http://sonarr:8989/api/v3/system/status").mock(
            side_effect=httpx.ConnectError("connection refused"),
        )
        async with _StubClient(url="http://sonarr:8989", api_key="k") as client:
            assert await client.ping() is None

    @pytest.mark.asyncio()
    @respx.mock
    async def test_ping_returns_none_on_invalid_json(self) -> None:
        """A 2xx with unparseable body collapses to None (ValueError swallowed)."""
        respx.get("http://sonarr:8989/api/v3/system/status").mock(
            return_value=httpx.Response(
                200,
                content=b"not-json",
                headers={"content-type": "application/json"},
            ),
        )
        async with _StubClient(url="http://sonarr:8989", api_key="k") as client:
            assert await client.ping() is None

    @pytest.mark.asyncio()
    async def test_ping_returns_none_on_invalid_url(self) -> None:
        """An unreachable scheme collapses to None (httpx.InvalidURL swallowed)."""
        client = _StubClient(url="not-a-url", api_key="k")
        try:
            assert await client.ping() is None
        finally:
            await client.aclose()

    @pytest.mark.asyncio()
    @respx.mock
    async def test_ping_returns_none_on_redirect_to_blocked_target(self) -> None:
        """A 3xx with a blocked Location still collapses to None.

        The response event_hook re-validates the Location header on
        every 3xx; targets that resolve to a blocked range raise
        ``ClientRedirectError``.  ``ping`` promises never to raise, so
        the error type is explicitly covered by ``_PING_SAFE_ERRORS``
        (via the common ``ClientError`` parent) and collapses to
        ``None`` alongside every other ping failure mode.
        """
        respx.get("http://sonarr:8989/api/v3/system/status").mock(
            return_value=httpx.Response(302, headers={"Location": "http://127.0.0.1/"}),
        )
        async with _StubClient(url="http://sonarr:8989", api_key="k") as client:
            assert await client.ping() is None


# get_instance_snapshot default


@pytest.mark.skip(
    reason="get_instance_snapshot and _count_unreleased_default moved to the adapter layer in PR8"
)
class TestInstanceSnapshotDefault:
    """Pin the default get_instance_snapshot contract.

    Skipped: PR8 (snapshot composition refactor) moved
    ``get_instance_snapshot`` and ``_count_unreleased_default`` off
    :class:`ArrClient` and onto the adapter layer
    (``adapter.fetch_instance_snapshot``).  The base class no longer
    exposes either; equivalent coverage lives next to the adapter.
    """

    @pytest.mark.asyncio()
    async def test_default_sums_missing_and_cutoff(self) -> None: ...

    @pytest.mark.asyncio()
    async def test_default_unreleased_count_is_zero(self) -> None: ...


# Context-manager lifecycle


class TestContextManager:
    """Pin __aenter__ / __aexit__ / aclose idempotency and delegation."""

    @pytest.mark.asyncio()
    async def test_aenter_returns_self(self) -> None:
        """async with yields the client itself (not the underlying httpx.AsyncClient)."""
        client = _StubClient(url="http://sonarr:8989", api_key="k")
        entered = await client.__aenter__()
        try:
            assert entered is client
        finally:
            await client.__aexit__(None, None, None)

    @pytest.mark.asyncio()
    async def test_aclose_closes_underlying_client(self) -> None:
        """aclose() marks the underlying httpx.AsyncClient as closed."""
        client = _StubClient(url="http://sonarr:8989", api_key="k")
        await client.aclose()
        assert client._client.is_closed is True

    @pytest.mark.asyncio()
    async def test_aclose_idempotent(self) -> None:
        """Double aclose does not raise."""
        client = _StubClient(url="http://sonarr:8989", api_key="k")
        await client.aclose()
        await client.aclose()  # second close is a no-op on httpx.AsyncClient
        assert client._client.is_closed is True


# Redirect-guard event hook (Phase 3b)


class TestRedirectGuard:
    """Pin the response event_hook that re-validates 3xx Location targets.

    ``follow_redirects=False`` is the primary defense; this hook is
    defense-in-depth.  It catches a blocked target BEFORE any hypothetical
    future flip of that kwarg could cause httpx to chase the redirect.
    """

    @pytest.mark.asyncio()
    @respx.mock
    async def test_redirect_to_blocked_target_raises(self) -> None:
        """A 302 pointing at 127.0.0.1 must raise ClientRedirectError."""
        from houndarr.errors import ClientRedirectError

        respx.get("http://sonarr:8989/ping").mock(
            return_value=httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"}),
        )
        async with _StubClient(url="http://sonarr:8989", api_key="k") as client:
            with pytest.raises(ClientRedirectError) as exc_info:
                await client._get("/ping")
        assert "127.0.0.1" in str(exc_info.value)

    @pytest.mark.asyncio()
    @respx.mock
    async def test_redirect_to_link_local_raises(self) -> None:
        """A 307 pointing at 169.254.169.254 (cloud metadata) must raise."""
        from houndarr.errors import ClientRedirectError

        respx.get("http://sonarr:8989/ping").mock(
            return_value=httpx.Response(
                307, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
            ),
        )
        async with _StubClient(url="http://sonarr:8989", api_key="k") as client:
            with pytest.raises(ClientRedirectError):
                await client._get("/ping")

    @pytest.mark.asyncio()
    @respx.mock
    async def test_relative_redirect_does_not_raise(self) -> None:
        """A relative Location (same host) is harmless and must not raise.

        Relative redirects inherit the *arr URL we already validated at
        connect time; the guard only matters for absolute targets that
        could change the destination host.
        """
        respx.get("http://sonarr:8989/ping").mock(
            return_value=httpx.Response(302, headers={"Location": "/other-endpoint"}),
        )
        async with _StubClient(url="http://sonarr:8989", api_key="k") as client:
            # The non-2xx still fails raise_for_status inside _get; we
            # only care that the guard does not fire first.
            with pytest.raises(httpx.HTTPStatusError):
                await client._get("/ping")

    @pytest.mark.asyncio()
    @respx.mock
    async def test_redirect_to_routable_target_does_not_raise(self) -> None:
        """A 3xx pointing at a routable public IP must NOT be blocked.

        The guard is narrow: only loopback, link-local, and unspecified
        addresses trip it.  Private ranges (10.*, 172.16.*, 192.168.*)
        are legitimate Docker / LAN destinations and must pass.
        """
        respx.get("http://sonarr:8989/ping").mock(
            return_value=httpx.Response(302, headers={"Location": "http://10.0.0.5/new"}),
        )
        async with _StubClient(url="http://sonarr:8989", api_key="k") as client:
            with pytest.raises(httpx.HTTPStatusError):
                # 302 without follow_redirects fails raise_for_status;
                # the guard allowed the response through.
                await client._get("/ping")
