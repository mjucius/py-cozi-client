"""
A stand-in for rest.cozi.com on a real socket, for contract tests.

aioresponses builds aiohttp responses by hand: it never fills the cookie jar, and it
broke outright on aiohttp 3.14. This stub is a real aiohttp server, so the client
runs exactly as it does against Cozi. It is strict in the ways Cozi is:

- a route matches its exact path (trailing slash included), as in the bundle;
- every request must carry ``apikey=coziwc|vNNN_production``, or it gets 400;
- authenticated routes need the current bearer token, or they get 401;
- login sets a ``cozi_refresh`` cookie; reauth trades it for a new token; logout
  revokes it.

Every request is recorded (method, path, query, headers, raw body, JSON body).

Usage::

    async def test_x(stub):
        stub.route("GET", "/2004/{acct}/list/", json=[...])   # register routes first
        async with stub.client() as cozi:                     # then start
            await cozi.get_lists()
        assert stub.sent("GET", "/2004/{acct}/list/")[0]["query"]["apikey"]

The ``stub`` fixture (tests/conftest.py) yields an unstarted StubCozi and stops it
afterwards.
"""

import contextlib
import json
import re
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

import aiohttp
from aiohttp import web

from cozi_client import CoziClient

APIKEY_RE = re.compile(r"^coziwc\|v\d+_production$")

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


class StubCozi:
    ACCOUNT = "acct-1"
    USERNAME = "user@example.com"
    PASSWORD = "pass"

    def __init__(self) -> None:
        self.app = web.Application(middlewares=[self._middleware])
        self.requests: List[Dict[str, Any]] = []
        self.token_counter = 0
        self.token = ""
        self.expires_in: Any = 3600
        self.refresh_cookie: Optional[str] = None
        self.logins = 0
        self.reauths = 0
        self.base_url = ""
        self._auth_modes: Dict[str, str] = {}
        self._runner: Optional[web.AppRunner] = None
        self.route("POST", "/2207/auth/login", self._login, auth="none")
        self.route("POST", "/2111/{acct}/auth/reauth", self._reauth, auth="none")
        self.route("POST", "/2111/{acct}/auth/logout", self._logout, auth="none")

    # ------------------------------------------------------------ registering

    def _full(self, path: str, unversioned: bool = False) -> str:
        return ("/api" if unversioned else "/api/ext") + path.replace("{acct}", self.ACCOUNT)

    def route(
        self,
        method: str,
        path: str,
        handler: Optional[Handler] = None,
        *,
        json: Any = None,
        status: int = 200,
        body: Optional[bytes] = None,
        auth: str = "bearer",
        unversioned: bool = False,
    ) -> None:
        """
        Serve ``method path`` (relative to /api/ext, or /api when ``unversioned``).

        Pass a handler, or a canned ``json`` / raw ``body`` + ``status``. ``auth`` is
        "bearer" (needs the current token), "query" (token in ``?auth=``, as the
        multipart uploads send it) or "none".
        """
        full = self._full(path, unversioned)
        if handler is None:

            async def handler(request: web.Request, _j=json, _s=status, _b=body):
                if _b is not None:
                    return web.Response(body=_b, status=_s)
                if _j is None:
                    return web.Response(status=_s if _s != 200 else 204)
                return web.json_response(_j, status=_s)

        self._auth_modes[f"{method.upper()} {full}"] = auth
        self.app.router.add_route(method.upper(), full, handler)

    def sent(self, method: str, path: str, unversioned: bool = False) -> List[Dict[str, Any]]:
        """Recorded requests to ``method path``, oldest first."""
        full = self._full(path, unversioned)
        return [r for r in self.requests if r["method"] == method.upper() and r["path"] == full]

    # --------------------------------------------------------------- running

    async def start(self) -> str:
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = self._runner.addresses[0][1]
        self.base_url = f"http://127.0.0.1:{port}"
        return self.base_url

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @contextlib.asynccontextmanager
    async def client(self, **kwargs: Any) -> AsyncIterator[CoziClient]:
        """
        Start the server (routes are frozen from then on) and yield a CoziClient
        pointed at it, with a cookie jar that accepts 127.0.0.1.
        """
        if not self.base_url:
            await self.start()
        session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
        cozi = CoziClient(self.USERNAME, self.PASSWORD, session=session, **kwargs)
        cozi.BASE_URL = self.base_url  # instance attribute shadows the class default
        try:
            yield cozi
        finally:
            await session.close()

    # ------------------------------------------------------------ middleware

    @web.middleware
    async def _middleware(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        raw = await request.read()
        parsed: Any = None
        if raw and request.content_type == "application/json":
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
        self.requests.append(
            {
                "method": request.method,
                "path": request.path,
                "query": dict(request.query),
                "headers": dict(request.headers),
                "cookies": dict(request.cookies),
                "body": raw,
                "json": parsed,
                "content_type": request.content_type,
            }
        )
        if not APIKEY_RE.match(request.query.get("apikey", "")):
            return web.json_response({"error": "missing apikey"}, status=400)
        mode = self._auth_modes.get(f"{request.method} {request.path}")
        if mode is None:
            # Unregistered route: let aiohttp answer 404 (or 405).
            return await handler(request)
        if mode == "bearer" and request.headers.get("Authorization") != f"Bearer {self.token}":
            return web.json_response({"error": "bad token"}, status=401)
        if mode == "query" and request.query.get("auth") != self.token:
            return web.json_response({"error": "bad token"}, status=401)
        return await handler(request)

    # ------------------------------------------------------------- auth routes

    def _new_token(self) -> Dict[str, Any]:
        self.token_counter += 1
        self.token = f"tok-{self.token_counter}"
        return {"accessToken": self.token, "accountId": self.ACCOUNT, "expiresIn": self.expires_in}

    async def _login(self, request: web.Request) -> web.Response:
        body = await request.json()
        if body.get("username") != self.USERNAME or body.get("password") != self.PASSWORD:
            return web.json_response({"error": "Invalid credentials"}, status=401)
        self.logins += 1
        response = web.json_response(self._new_token())
        if body.get("issueRefresh"):
            self.refresh_cookie = f"refresh-{self.token_counter}"
            response.set_cookie("cozi_refresh", self.refresh_cookie, path="/", httponly=True)
        return response

    async def _reauth(self, request: web.Request) -> web.Response:
        if (
            self.refresh_cookie is None
            or request.cookies.get("cozi_refresh") != self.refresh_cookie
            or "Authorization" in request.headers
        ):
            return web.json_response({"error": "refresh refused"}, status=401)
        self.reauths += 1
        return web.json_response(self._new_token())

    async def _logout(self, request: web.Request) -> web.Response:
        if request.cookies.get("cozi_refresh") == self.refresh_cookie:
            self.refresh_cookie = None
        return web.Response(status=200)
