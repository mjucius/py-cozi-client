"""
HTTP transport and authentication, shared by every part of the client.

Mirrors how the production web client (my.cozi.com, bundle main.c43180f9) talks to
rest.cozi.com: every request carries ``apikey=coziwc|vNNN_production``, authenticated
calls carry ``Authorization: Bearer <token>``, and the token is renewed through the
``cozi_refresh`` cookie shortly before it expires.
"""

import asyncio
import json as jsonlib
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, TypeVar

import aiohttp
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from yarl import URL

from .exceptions import (
    APIError,
    AuthenticationError,
    ConflictError,
    CoziException,
    CoziValidationError,
    NetworkError,
    PermissionDeniedError,
    RateLimitError,
    ResourceNotFoundError,
    ResponseParseError,
)

logger = logging.getLogger("cozi_client")

# The only shape an id may take before it is put in a request path.
#
# Ids are interpolated into URL paths and into JSON-Pointer `path`s of PATCH
# operations. A `../` would escape the intended prefix (aiohttp normalises dot
# segments), aiming a request that carries the bearer token at another path; `?`
# or `#` would truncate the path; `/` or `~` would retarget a JSON-Pointer.
# Restricting ids to unreserved characters closes all of it at once. Every id the
# production API hands out (uuids, 40-hex calendar ids) fits.
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


# The web app's upload cap for photos (one shared constant, 2e7, in bundle main.c43180f9).
MAX_PHOTO_BYTES = 20_000_000


def validate_id(value: Any, name: str = "id") -> str:
    """Return ``value`` if it is a well-formed Cozi id, else raise CoziValidationError."""
    # fullmatch, not match: Python's `$` also matches before a trailing newline.
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise CoziValidationError(f"Invalid {name}: {value!r}")
    return value


def validate_calendar_period(year: Any, month: Any) -> Tuple[int, int]:
    """Return ``(year, month)`` if both are ints in range; they end up in a URL path."""
    # Any 4-digit year: Cozi files a birthday under its birth year (1904 when none is given).
    for name, value, low, high in (("year", year, 1000, 9999), ("month", month, 1, 12)):
        # bool is an int subclass; True would otherwise pass as month 1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise CoziValidationError(f"Invalid {name}: expected an integer, got {value!r}")
        if not low <= value <= high:
            raise CoziValidationError(f"Invalid {name}: {value} is not in {low}-{high}")
    return year, month


M = TypeVar("M", bound=BaseModel)


def parse_model(model: Type[M], data: Any) -> M:
    """Validate a response body, raising ResponseParseError instead of pydantic's error."""
    try:
        return model.model_validate(data)
    except PydanticValidationError as e:
        raise ResponseParseError(
            f"Unexpected {model.__name__} shape from Cozi: {e.error_count()} validation error(s)",
            response_data=data if isinstance(data, dict) else {"response": data},
        ) from e


def parse_models(model: Type[M], data: Any) -> List[M]:
    """Validate a JSON array of objects; anything that is not an array is an error."""
    if not isinstance(data, list):
        raise ResponseParseError(
            f"Expected a list of {model.__name__} from Cozi, got {type(data).__name__}"
        )
    return [parse_model(model, item) for item in data]


# Safe to replay after a network error or 5xx. POST/PUT/PATCH are not: Cozi may have
# applied them before the failure surfaced, and replaying would double-apply the write.
# 401 and 429 are replayed for every method, since both are refusals issued before
# the request was applied.
IDEMPOTENT_METHODS = frozenset({"GET", "DELETE"})

JSON_CONTENT_TYPE = {"Content-Type": "application/json; charset=utf-8"}


def _reason(body: Any) -> str:
    if isinstance(body, dict):
        # Cozi puts the specific reason in `detail` when it sends one (e.g. a failed recipe
        # import: error is a generic framework sentence, detail says "Could not retrieve recipe.").
        for key in ("detail", "error", "message"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return f": {value[:300]}"
    return ""


class Transport:
    """Session, request, retry and token handling. Not used directly; see CoziClient."""

    BASE_URL = "https://rest.cozi.com"
    # Production computes this build string at runtime (v264 + 1 in bundle main.c43180f9);
    # Cozi accepts any "coziwc|vNNN_production" value but rejects requests without one.
    APIKEY = "coziwc|v265_production"
    # Cloudflare in front of rest.cozi.com refuses requests that do not look like they
    # come from the web app.
    BROWSER_HEADERS = {
        "Origin": "https://my.cozi.com",
        "Referer": "https://my.cozi.com/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0 Safari/537.36"
        ),
    }
    # Renew this many seconds before the token expires (the web client's margin).
    REFRESH_MARGIN = 180
    MIN_REQUEST_INTERVAL = 0.1

    def __init__(
        self,
        username: str,
        password: str,
        session: Optional[aiohttp.ClientSession] = None,
        retry_attempts: int = 3,
        request_timeout: float = 30,
    ):
        """
        Args:
            username: Cozi account email
            password: Cozi account password
            session: Optional aiohttp session. Use one session per Cozi account: the
                refresh cookie lives in the session's cookie jar.
            retry_attempts: Attempts per request for retriable failures (at least 1)
            request_timeout: Total seconds allowed per HTTP attempt
        """
        self.username = username
        self.password = password
        self._session = session
        self._own_session = session is None
        self.retry_attempts = max(1, retry_attempts)
        self.request_timeout = request_timeout

        self._access_token: Optional[str] = None
        self._account_id: Optional[str] = None
        self._expires_in: Optional[float] = None
        self._issued_at = 0.0
        # Bumped on every accepted token, so a request can tell whether a new one
        # arrived while it was in flight.
        self._token_generation = 0
        self._auth_lock = asyncio.Lock()

        self._gate_lock = asyncio.Lock()
        self._last_request_time = 0.0

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self) -> None:
        """Close the HTTP session if the client created it."""
        if self._own_session and self._session:
            await self._session.close()
            self._session = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar())
        return self._session

    @property
    def account_id(self) -> Optional[str]:
        """The Cozi account (household) id, once authenticated."""
        return self._account_id

    # ------------------------------------------------------------------ auth

    async def authenticate(self) -> None:
        """
        Log in with the username and password.

        Optional: every method logs in on first use. With ``issueRefresh`` Cozi also
        sets a ``cozi_refresh`` cookie, which later renews the token without the
        password, as the web client does. Like the web client, a session renewed that
        way may outlive a password change made elsewhere.
        """
        async with self._auth_lock:
            await self._login()

    async def logout(self) -> None:
        """
        End the session on the server (POST /2111/{acct}/auth/logout) and forget it locally.

        The next call logs in again with the stored password. Server errors are
        ignored, as the web client ignores them.
        """
        async with self._auth_lock:
            if self._account_id and self._session is not None:
                try:
                    await self._request_with_status(
                        "POST",
                        "/2111/{acct}/auth/logout",
                        data=b"",
                        headers=JSON_CONTENT_TYPE,
                        auth=False,
                    )
                except CoziException as e:
                    logger.info("Server-side logout failed (%s); clearing local state", type(e).__name__)
            self._access_token = None
            self._account_id = None
            self._expires_in = None
            host = URL(self.BASE_URL).host
            if self._session is not None and host:
                self._session.cookie_jar.clear_domain(host)

    async def change_password(self, new_password: str) -> None:
        """
        Change the account password (POST /2111/{acct}/auth/password).

        Cozi answers with a new token, which replaces the current one, and the client
        keeps using ``new_password`` for later logins.
        """
        if not isinstance(new_password, str) or not new_password:
            raise CoziValidationError("New password cannot be empty")
        body = await self._request(
            "POST", "/2111/{acct}/auth/password", json={"newPassword": new_password}
        )
        async with self._auth_lock:
            self._accept_token(body, account_id=self._account_id)
            self.password = new_password

    async def request_password_reset(self, email: str) -> None:
        """Ask Cozi to email a password-reset link (no login needed)."""
        if not isinstance(email, str) or not email.strip():
            raise CoziValidationError("Email cannot be empty")
        await self._request(
            "POST",
            "/2110/household/password/resetrequest",
            json={"email": email.strip()},
            auth=False,
        )

    async def submit_password_reset(
        self, account_id: str, request_id: str, new_password: str
    ) -> None:
        """Set a new password from the ids in a reset link (no login needed)."""
        account_id = validate_id(account_id, "account_id")
        request_id = validate_id(request_id, "request_id")
        if not isinstance(new_password, str) or not new_password:
            raise CoziValidationError("New password cannot be empty")
        await self._request(
            "PUT",
            f"/2110/{account_id}/household/password/resetrequest/{request_id}",
            json={"newPassword": new_password},
            auth=False,
        )

    async def _login(self) -> None:
        logger.info("Logging in to Cozi")
        _, body = await self._request_with_status(
            "POST",
            "/2207/auth/login",
            json={"username": self.username, "password": self.password, "issueRefresh": True},
            auth=False,
        )
        self._accept_token(body)

    async def _reauth(self) -> None:
        # The web client posts an empty body with a JSON content type and no Bearer;
        # the cozi_refresh cookie in the jar is the credential.
        _, body = await self._request_with_status(
            "POST",
            "/2111/{acct}/auth/reauth",
            data=b"",
            headers=JSON_CONTENT_TYPE,
            auth=False,
        )
        self._accept_token(body, account_id=self._account_id)

    def _accept_token(self, body: Any, account_id: Optional[str] = None) -> None:
        # Never echo the body: on success it holds the token.
        if not isinstance(body, dict):
            raise AuthenticationError(
                f"Invalid login response: expected an object, got {type(body).__name__}"
            )
        if body.get("error"):
            raise AuthenticationError(f"Cozi refused the login{_reason(body)}")
        token = body.get("accessToken")
        account = body.get("accountId") or account_id
        missing = [n for n, v in (("accessToken", token), ("accountId", account)) if not v]
        if missing:
            raise AuthenticationError(f"Invalid login response: missing {' and '.join(missing)}")
        expires_in = body.get("expiresIn")
        self._access_token = token
        self._account_id = account
        self._expires_in = expires_in if isinstance(expires_in, (int, float)) else None
        self._issued_at = time.monotonic()
        self._token_generation += 1

    def _token_expiring(self) -> bool:
        if self._expires_in is None:
            return False
        return time.monotonic() - self._issued_at >= self._expires_in - self.REFRESH_MARGIN

    async def _renew(self, seen_generation: int, refresh: bool) -> None:
        """
        Get a token, once, however many requests ask at the same time.

        A caller that waited on the lock while another renewed finds the generation
        moved on and uses that token. Cookie refresh falls back to the password.
        """
        async with self._auth_lock:
            if self._access_token and self._token_generation != seen_generation:
                return
            if refresh and self._account_id:
                try:
                    await self._reauth()
                    return
                except CoziException as e:
                    logger.info("Token refresh refused (%s); logging in again", type(e).__name__)
            await self._login()

    async def _ensure_authenticated(self) -> None:
        seen = self._token_generation
        if not self._access_token:
            await self._renew(seen, refresh=False)
        elif self._token_expiring():
            await self._renew(seen, refresh=True)

    # --------------------------------------------------------------- requests

    async def _gate(self) -> None:
        async with self._gate_lock:
            wait = self._last_request_time + self.MIN_REQUEST_INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_time = time.monotonic()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Send a request and return its decoded body. See _request_with_status."""
        _, body = await self._request_with_status(method, path, **kwargs)
        return body

    async def _request_with_status(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: Any = None,
        form: Optional[Callable[[], aiohttp.FormData]] = None,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
        auth: bool = True,
        query_token: bool = False,
        unversioned: bool = False,
        raw: bool = False,
    ) -> Tuple[int, Any]:
        """
        Send one API request with apikey, auth, retries and error mapping.

        Args:
            path: Path under /api/ext (or /api when ``unversioned``), e.g.
                "/2004/{acct}/list/". "{acct}" becomes the account id.
            json: JSON body. ``data`` sends raw bytes instead; ``form`` builds a
                multipart body (a factory, since a FormData is single-use).
            auth: Send the bearer token (logging in first if needed) and replay
                once with a renewed token on 401.
            query_token: Put the token in the ``auth`` query parameter instead of a
                header, as the web client's multipart uploads do.
            raw: Return the body as bytes instead of decoding JSON.

        Returns:
            (status, body): body is the decoded JSON, ``True`` for an empty body, or
            bytes when ``raw``.
        """
        session = await self._ensure_session()
        if auth:
            await self._ensure_authenticated()
        if "{acct}" in path:
            if not self._account_id:
                raise AuthenticationError("Not logged in")
            path = path.replace("{acct}", self._account_id)
        url = f"{self.BASE_URL}{'/api' if unversioned else '/api/ext'}{path}"
        method = method.upper()
        retriable = method in IDEMPOTENT_METHODS
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)

        attempt = 0
        replayed_401 = False
        previous_5xx = False
        while True:
            await self._gate()
            sent_generation = self._token_generation
            query = {"apikey": self.APIKEY, **(params or {})}
            request_headers = {**self.BROWSER_HEADERS, **(headers or {})}
            if auth and query_token:
                query["auth"] = self._access_token or ""
            elif auth:
                request_headers["Authorization"] = f"Bearer {self._access_token}"
            body_kwargs: Dict[str, Any] = {}
            if form is not None:
                body_kwargs["data"] = form()
            elif data is not None:
                body_kwargs["data"] = data
            elif json is not None:
                body_kwargs["json"] = json
            logger.debug("%s %s", method, path)

            try:
                async with session.request(
                    method,
                    url,
                    params=query,
                    headers=request_headers,
                    timeout=timeout,
                    # Per request, so an injected session's raise_for_status=True
                    # cannot bypass the status handling below.
                    raise_for_status=False,
                    **body_kwargs,
                ) as response:
                    status = response.status
                    payload = await response.read()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                # Never format the exception: a ClientResponseError's str and repr hold
                # the request URL (?auth= on uploads) and headers (the bearer token).
                detail = type(e).__name__
                if isinstance(e, aiohttp.ClientResponseError):
                    detail += f" {e.status}"
                if retriable and attempt < self.retry_attempts - 1:
                    wait = 0.5 * 2**attempt
                    attempt += 1
                    logger.warning("Network error on %s %s, retrying in %ss: %s", method, path, wait, detail)
                    await asyncio.sleep(wait)
                    continue
                # from None: a chained cause would print the same URL and headers.
                raise NetworkError(f"Network request failed: {detail}") from None

            if 200 <= status < 300:
                return status, self._decode(method, path, status, payload, raw)

            error_body = self._decode_error(payload)

            if status == 401 and auth and not replayed_401:
                # Replaying after a renewed token does not use up a retry attempt.
                replayed_401 = True
                logger.info("401 on %s %s; renewing the token", method, path)
                await self._renew(sent_generation, refresh=True)
                continue
            if status == 429 and attempt < self.retry_attempts - 1:
                wait = 1.0 * 2**attempt
                attempt += 1
                logger.warning("Rate limited on %s %s, waiting %ss", method, path, wait)
                await asyncio.sleep(wait)
                continue
            if 500 <= status < 600 and retriable and attempt < self.retry_attempts - 1:
                wait = 1.0 * 2**attempt
                attempt += 1
                previous_5xx = True
                logger.warning("Server error %s on %s %s, retrying in %ss", status, method, path, wait)
                await asyncio.sleep(wait)
                continue
            if status == 404 and method == "DELETE" and previous_5xx:
                # The first attempt reached the server and was applied before the
                # 5xx surfaced; the replay finding nothing is the delete succeeding.
                return 204, True
            raise self._error_for(status, error_body)

    @staticmethod
    def _decode(method: str, path: str, status: int, payload: bytes, raw: bool) -> Any:
        if raw:
            return payload
        if status == 204 or not payload.strip():
            return True
        try:
            return jsonlib.loads(payload)
        except ValueError:
            raise ResponseParseError(
                f"{method} {path} answered {status} with a body that is not JSON",
                status_code=status,
            ) from None

    @staticmethod
    def _decode_error(payload: bytes) -> Dict[str, Any]:
        try:
            body = jsonlib.loads(payload)
        except ValueError:
            return {"error": "No JSON content in error response"}
        return body if isinstance(body, dict) else {"response": body}

    @staticmethod
    def _error_for(status: int, body: Dict[str, Any]) -> CoziException:
        reason = _reason(body)
        if status == 401:
            return AuthenticationError(f"Authentication failed{reason}", status, body)
        if status == 402:
            return PermissionDeniedError(
                f"Payment required: this feature needs a paid Cozi plan{reason}", status, body
            )
        if status == 403:
            return PermissionDeniedError(
                f"Access forbidden (some features need a paid Cozi plan){reason}", status, body
            )
        if status == 404:
            return ResourceNotFoundError(f"Resource not found{reason}", status, body)
        if status in (409, 410):
            return ConflictError(
                f"Someone else changed this data; read it again and retry{reason}", status, body
            )
        if status == 429:
            return RateLimitError(f"API rate limit exceeded{reason}", status, body)
        if 500 <= status < 600:
            return APIError(f"Server error: {status}{reason}", status, body)
        return APIError(f"API request failed: {status}{reason}", status, body)
