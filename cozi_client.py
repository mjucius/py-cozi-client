"""
Enhanced Cozi Family Organizer API Client

This module provides a comprehensive, async client for interacting with the Cozi API.
"""

import asyncio
import logging
import time as time_module
from datetime import datetime, date, time
from typing import List, Optional, Dict, Any, Tuple, Union
from urllib.parse import urljoin

import aiohttp
from pydantic import ValidationError as PydanticValidationError

from exceptions import (
    CoziException,
    AuthenticationError,
    RateLimitError,
    APIError,
    NetworkError,
    ResourceNotFoundError,
    PermissionDeniedError,
    ValidationError,
    WriteVerificationError,
)
from models import (
    ListType,
    ItemStatus,
    CoziList,
    CoziItem,
    CoziAppointment,
    CoziPerson,
)

logger = logging.getLogger(__name__)

# Methods that are safe to replay after a network error or 5xx.
#
# POST/PUT/PATCH are excluded: Cozi applies them server-side before the failure
# surfaces here, so a blind replay can double-apply the write (two appointments,
# a duplicated item). Retrying those safely would need idempotency keys the API
# does not offer. 401 and 429 are still retried for every method — both are
# rejections issued before the request was applied, so no partial write can have
# landed.
IDEMPOTENT_METHODS = frozenset({"GET", "DELETE", "HEAD", "OPTIONS"})


def _assert_not_rejected(
    response: Any, operation: str, appointment_id: Optional[str] = None
) -> None:
    """
    Raise if Cozi discarded a calendar operation.

    The calendar endpoint answers HTTP 200 even when it refuses an operation,
    naming the reason only in a ``rejectedItems`` array. Verified against live
    Cozi 2026-07-24 — e.g. an edit carrying an unexpected attribute comes back as::

        {"rejectedItems": [{"operation": "edit", "id": "...",
          "error": "Operation rejected due to request data problem. Detail:
                    Unexpected attribute 'item_version' for AppointmentResource"}]}

    while the object the caller holds still looks perfectly correct. Without this
    check a discarded write is indistinguishable from a real one.
    """
    if not isinstance(response, dict):
        return

    rejected = response.get("rejectedItems")
    if not isinstance(rejected, list) or not rejected:
        return

    if appointment_id:
        # A rejection with no id is unattributable, so treat it as ours rather
        # than discarding it silently.
        mine = [
            r
            for r in rejected
            if isinstance(r, dict) and r.get("id") in (None, appointment_id)
        ]
    else:
        mine = rejected
    if not mine:
        return

    reasons = "; ".join(
        r.get("error") if isinstance(r, dict) and isinstance(r.get("error"), str)
        else "no reason given"
        for r in mine
    )
    target = f" for appointment {appointment_id}" if appointment_id else ""
    raise WriteVerificationError(
        f"Cozi rejected the {operation} operation{target}: {reasons}",
        response_data=response,
    )


class CoziClient:
    """
    Enhanced async client for the Cozi Family Organizer API.
    
    This client provides comprehensive access to Cozi's features including:
    - List management (shopping and todo lists)
    - Item management with full CRUD operations
    - Calendar and appointment management
    - Account and family member management
    - Proper authentication and error handling
    """
    
    BASE_URL = "https://rest.cozi.com"
    API_VERSION = "2004"
    AUTH_VERSION = "2207"
    # Required by Cloudflare/Cozi as of 2026-04 — without this query param the
    # login endpoint returns 401. Discovered upstream in Wetzel402/py-cozi PR #3
    # by capturing traffic from the live my.cozi.com web client. Any
    # "coziwc|vNNN_production" value is accepted; the live web bundle currently
    # ships v257_production.
    APIKEY = "coziwc|v251_production"
    # Browser-like headers to satisfy Cloudflare's bot detection on rest.cozi.com.
    # Without these, requests from server environments (Smithery, CI) get 401
    # at the auth layer even with correct credentials.
    BROWSER_HEADERS = {
        "Origin": "https://my.cozi.com",
        "Referer": "https://my.cozi.com/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/130.0 Safari/537.36"
        ),
    }
    
    def __init__(
        self,
        username: str,
        password: str,
        session: Optional[aiohttp.ClientSession] = None,
        retry_attempts: int = 3,
        request_timeout: int = 30,
    ):
        """
        Initialize the Cozi client.
        
        Args:
            username: Cozi account username/email
            password: Cozi account password
            session: Optional aiohttp session to use
            retry_attempts: Number of retry attempts for failed requests
            request_timeout: Request timeout in seconds
        """
        self.username = username
        self.password = password
        self._session = session
        self._own_session = session is None
        self.retry_attempts = retry_attempts
        self.request_timeout = request_timeout
        
        # Authentication state
        self._access_token: Optional[str] = None
        self._token_expires: Optional[int] = None
        self._account_id: Optional[str] = None
        self._authenticated = False
        
        # Rate limiting
        self._last_request_time = 0.0
        self._min_request_interval = 0.1  # Minimum time between requests
        
        # Debug information
        self._last_request_data: Optional[Dict[str, Any]] = None
        self._last_response_data: Optional[Dict[str, Any]] = None
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self._ensure_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
    
    async def _ensure_session(self):
        """Ensure we have a valid aiohttp session."""
        if not self._session:
            timeout = aiohttp.ClientTimeout(total=self.request_timeout)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                cookie_jar=aiohttp.CookieJar()
            )
    
    async def close(self):
        """Close the HTTP session if we own it."""
        if self._own_session and self._session:
            await self._session.close()
            self._session = None
    
    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers."""
        if not self._access_token:
            return {}
        return {"Authorization": f"Bearer {self._access_token}"}
    
    def get_last_request_data(self) -> Optional[Dict[str, Any]]:
        """Get the last API request data for debugging."""
        return self._last_request_data
    
    def get_last_response_data(self) -> Optional[Dict[str, Any]]:
        """Get the last API response data for debugging."""
        return self._last_response_data
    
    async def _ensure_authenticated(self) -> None:
        """Ensure authentication is complete before using account_id."""
        if not self._authenticated:
            await self.authenticate()
    
    async def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Any] = None,
        params: Optional[Dict[str, str]] = None,
        require_auth: bool = True,
    ) -> Union[Dict[str, Any], List[Any], bool]:
        """
        Make an authenticated API request with retry logic.

        Args:
            method: HTTP method
            endpoint: API endpoint (without base URL)
            data: JSON data to send
            params: Query parameters
            require_auth: Whether authentication is required

        Returns:
            JSON response data

        Raises:
            Various CoziException subclasses based on error type
        """
        _, response_data = await self._make_request_with_status(
            method, endpoint, data=data, params=params, require_auth=require_auth
        )
        return response_data

    async def _make_request_with_status(
        self,
        method: str,
        endpoint: str,
        data: Optional[Any] = None,
        params: Optional[Dict[str, str]] = None,
        require_auth: bool = True,
    ) -> Tuple[int, Union[Dict[str, Any], List[Any], bool]]:
        """
        Same as :meth:`_make_request`, but also returns the HTTP status code.

        Needed where 200 and 201 mean materially different things. Updating a list
        item is the case in point: Cozi answers 200 when it updated an existing item
        and 201 when the id did not exist and it *created* one instead. The response
        bodies are identical, so the status is the only way to tell an update from
        an accidental insert.

        Returns:
            (status_code, JSON response data)
        """
        await self._ensure_session()
        
        if require_auth and not self._authenticated:
            logger.debug(f"Not authenticated, calling authenticate(). Current account_id: {self._account_id}")
            await self.authenticate()
            logger.debug(f"After authenticate(). New account_id: {self._account_id}")
        
        # Rate limiting
        now = time_module.monotonic()
        time_since_last = now - self._last_request_time
        if time_since_last < self._min_request_interval:
            await asyncio.sleep(self._min_request_interval - time_since_last)
        
        url = urljoin(self.BASE_URL, endpoint)
        headers = dict(self.BROWSER_HEADERS)
        if require_auth:
            headers.update(self._get_auth_headers())
        retriable = method.upper() in IDEMPOTENT_METHODS
        logger.debug(f"Making request to: {url} (account_id: {self._account_id})")
        
        # Store request data for debugging (excluding sensitive auth headers)
        self._last_request_data = {
            "method": method,
            "url": url,
            "data": data,
            "params": params,
        }
        
        for attempt in range(self.retry_attempts):
            try:
                self._last_request_time = time_module.monotonic()
                
                async with self._session.request(
                    method,
                    url,
                    json=data,
                    params=params,
                    headers=headers
                ) as response:
                    # Handle responses with content (200, 201)
                    if response.status in (200, 201):
                        response_data = await response.json()
                        self._last_response_data = response_data
                        logger.debug(f"API request successful: {method} {endpoint} (status: {response.status})")
                        return response.status, response_data

                    # Handle successful responses with no content (204)
                    elif response.status == 204:
                        self._last_response_data = None
                        logger.debug(f"API request successful: {method} {endpoint} (status: {response.status}, no content)")
                        return response.status, True  # True indicates successful operation
                    
                    # Handle error responses - parse JSON for all error cases
                    else:
                        try:
                            response_data = await response.json()
                        except (aiohttp.ContentTypeError, ValueError):
                            response_data = {"error": "No JSON content in error response"}
                        
                        # Store response data for debugging
                        self._last_response_data = response_data
                        
                        if response.status == 401:
                            if attempt == 0 and require_auth:
                                # Safe to replay for any method: a 401 is issued
                                # before the request is applied, so no partial
                                # write can have landed.
                                logger.info("Authentication failed, retrying login")
                                self._authenticated = False
                                await self.authenticate()
                                # update(), not assignment: BROWSER_HEADERS must
                                # survive or Cloudflare 401s the retry as well.
                                headers.update(self._get_auth_headers())
                                continue
                            else:
                                raise AuthenticationError(
                                    "Authentication failed",
                                    status_code=response.status,
                                    response_data=response_data
                                )
                        elif response.status == 403:
                            raise PermissionDeniedError(
                                "Access forbidden",
                                status_code=response.status,
                                response_data=response_data
                            )
                        elif response.status == 404:
                            raise ResourceNotFoundError(
                                "Resource not found",
                                status_code=response.status,
                                response_data=response_data
                            )
                        elif response.status == 429:
                            # Also safe to replay for any method — rejected before
                            # it was applied.
                            if attempt < self.retry_attempts - 1:
                                # Exponential backoff for rate limiting
                                wait_time = (2 ** attempt) * 1.0
                                logger.warning(f"Rate limited, waiting {wait_time}s")
                                await asyncio.sleep(wait_time)
                                continue
                            else:
                                raise RateLimitError(
                                    "API rate limit exceeded",
                                    status_code=response.status,
                                    response_data=response_data
                                )
                        elif 500 <= response.status < 600:
                            if retriable and attempt < self.retry_attempts - 1:
                                wait_time = (2 ** attempt) * 1.0
                                logger.warning(f"Server error {response.status}, retrying in {wait_time}s")
                                await asyncio.sleep(wait_time)
                                continue
                            else:
                                raise APIError(
                                    f"Server error: {response.status}",
                                    status_code=response.status,
                                    response_data=response_data
                                )
                        else:
                            raise APIError(
                                f"API request failed: {response.status}",
                                status_code=response.status,
                                response_data=response_data
                            )
            
            except aiohttp.ClientError as e:
                if retriable and attempt < self.retry_attempts - 1:
                    wait_time = (2 ** attempt) * 0.5
                    logger.warning(f"Network error, retrying in {wait_time}s: {e}")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    raise NetworkError(f"Network request failed: {e}")
        
        raise APIError("Max retry attempts exceeded")
    
    async def authenticate(self) -> None:
        """
        Authenticate with the Cozi API and store access token.
        
        Raises:
            AuthenticationError: If login fails
        """
        logger.info("Authenticating with Cozi API")
        
        response = await self._make_request(
            "POST",
            f"/api/ext/{self.AUTH_VERSION}/auth/login",
            data={
                "username": self.username,
                "password": self.password,
                "issueRefresh": True,
            },
            params={"apikey": self.APIKEY},
            require_auth=False
        )
        
        # Deliberately not logged in full: the login response carries the access
        # token and refresh token, and debug logs routinely end up in bug reports.
        logger.debug(
            "Authentication response keys: %s",
            sorted(response) if isinstance(response, dict) else type(response).__name__,
        )
        if not isinstance(response, dict):
            raise AuthenticationError(
                f"Invalid login response: expected an object, got {type(response).__name__}"
            )

        self._access_token = response.get("accessToken")
        self._token_expires = response.get("expiresIn")
        self._account_id = response.get("accountId")

        logger.debug(f"Parsed auth data - token: {self._access_token is not None}, account_id: {self._account_id}")

        if not all([self._access_token, self._account_id]):
            # Name the missing field only — echoing the response back would leak
            # any credential material it did contain into the exception message.
            missing = " and ".join(
                name
                for name, value in (
                    ("accessToken", self._access_token),
                    ("accountId", self._account_id),
                )
                if not value
            )
            raise AuthenticationError(f"Invalid login response: missing {missing}")


        self._authenticated = True
        logger.info("Successfully authenticated with Cozi API")

    async def logout(self) -> None:
        """
        Clear local authentication state.

        The Cozi auth API does not expose a server-side token revocation endpoint,
        so this only clears in-process credentials. The next API call will
        re-authenticate using the username/password supplied at construction time.
        """
        self._access_token = None
        self._token_expires = None
        self._account_id = None
        self._authenticated = False
        logger.info("Cleared local Cozi authentication state")

    # Account and Person Management
    
    async def get_family_members(self) -> List[CoziPerson]:
        """
        Get all family members/persons in the account.
        
        Returns:
            List of CoziPerson objects
        """
        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/account/person/"
        response = await self._make_request("GET", endpoint)
        
        if isinstance(response, list):
            return [CoziPerson.model_validate(person) for person in response]
        return []
    
    # List Management
    
    async def get_lists(self) -> List[CoziList]:
        """
        Get all lists (shopping and todo lists).
        
        Returns:
            List of CoziList objects
        """
        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/list/"
        response = await self._make_request("GET", endpoint)
        
        if isinstance(response, list):
            return [CoziList.model_validate(list_data) for list_data in response]
        return []
    
    async def get_lists_by_type(self, list_type: ListType) -> List[CoziList]:
        """
        Get lists filtered by type.

        Args:
            list_type: Type of lists to retrieve

        Returns:
            List of CoziList objects of the specified type
        """
        all_lists = await self.get_lists()
        # use_enum_values stores list_type as the string value, not the enum.
        return [lst for lst in all_lists if lst.list_type == list_type.value]
    
    async def create_list(self, title: str, list_type: ListType) -> CoziList:
        """
        Create a new list.
        
        Args:
            title: List title
            list_type: Type of list to create
        
        Returns:
            Created CoziList object
        """
        if not title.strip():
            raise ValidationError("List title cannot be empty")
        
        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/list/"
        response = await self._make_request(
            "POST",
            endpoint,
            data={"title": title, "listType": list_type.value}
        )
        
        return CoziList.model_validate(response)
    
    async def update_list(self, list_obj: CoziList) -> CoziList:
        """
        Update an existing list (mainly for reordering items).
        
        Args:
            list_obj: CoziList object to update
        
        Returns:
            Updated CoziList object
        """
        if not list_obj.id:
            raise ValidationError("Cannot update list without ID")
        
        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/list/{list_obj.id}"
        
        # Convert items to API format
        items_data = []
        for item in list_obj.items:
            item_dict = {
                "text": item.text,
                "status": item.status.value,
            }
            if item.id:
                item_dict["id"] = item.id
            if item.position is not None:
                item_dict["position"] = item.position
            items_data.append(item_dict)
        
        data = {
            "externalIds": [],
            "title": list_obj.title,
            "items": items_data,
            "notes": None,
            "listId": list_obj.id,
            "version": list_obj.version,
            "owner": list_obj.owner,
            "listType": list_obj.list_type.value,
        }
        
        response = await self._make_request("PUT", endpoint, data=data)
        return CoziList.model_validate(response)
    
    async def delete_list(self, list_id: str) -> bool:
        """
        Delete a list.
        
        Args:
            list_id: ID of the list to delete
        
        Returns:
            True if deletion was successful
        """
        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/list/{list_id}"
        await self._make_request("DELETE", endpoint)
        return True
    
    # Item Management
    
    async def add_item(self, list_id: str, text: str, position: int = 0) -> CoziItem:
        """
        Add an item to a list.
        
        Args:
            list_id: ID of the list to add item to
            text: Item text
            position: Position in the list (0 = top)
        
        Returns:
            Created CoziItem object
        """
        if not text.strip():
            raise ValidationError("Item text cannot be empty")
        
        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/list/{list_id}/item/"
        response = await self._make_request(
            "POST",
            endpoint,
            data={"text": text, "position": position}
        )

        item = CoziItem.model_validate(response)
        if item.text != text or not item.id:
            detail = "" if item.id else " with no id"
            raise WriteVerificationError(
                f"Cozi did not apply the item creation: asked for text {text!r}, "
                f"server returned {item.text!r}{detail}",
                response_data=response if isinstance(response, dict) else None,
            )
        return item

    async def _put_item(
        self, list_id: str, item_id: str, data: Dict[str, Any]
    ) -> CoziItem:
        """
        PUT an item, rejecting the case where Cozi *created* it instead of updating.

        Verified against live Cozi 2026-07-24 via the parallel TypeScript client in
        cozi_mcp: a PUT to an item id that does not exist answers 201 and persists a
        brand-new item under that exact id — an upsert, not an error. A stale or
        mistyped id would therefore silently add a phantom item to the user's list
        rather than failing. 200 vs 201 is the only signal; the bodies are identical.

        On 201 the phantom is deleted before raising, so a failed update leaves no
        residue. If that cleanup itself fails the original error still surfaces —
        losing it to report a cleanup problem would be worse — noting that the item
        may remain.
        """
        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/list/{list_id}/item/{item_id}"
        status, response = await self._make_request_with_status("PUT", endpoint, data=data)

        if status == 201:
            try:
                await self.remove_items(list_id, [item_id])
                cleanup = " The phantom item was removed."
            except CoziException as e:
                logger.warning(f"Failed to clean up phantom item {item_id}: {e}")
                cleanup = " The phantom item could NOT be removed and may still be in the list."
            raise ResourceNotFoundError(
                f"Item {item_id} does not exist in list {list_id}; Cozi created a "
                f"new item instead of updating it.{cleanup}",
                status_code=status,
            )

        return CoziItem.model_validate(response)

    async def update_item_text(self, list_id: str, item_id: str, text: str) -> CoziItem:
        """
        Update the text of a list item.

        Args:
            list_id: ID of the list containing the item
            item_id: ID of the item to update
            text: New item text

        Returns:
            Updated CoziItem object

        Raises:
            ResourceNotFoundError: item_id does not exist (Cozi upserted instead)
            WriteVerificationError: server did not apply the new text
        """
        if not text.strip():
            raise ValidationError("Item text cannot be empty")

        item = await self._put_item(list_id, item_id, {"text": text})
        if item.text != text:
            raise WriteVerificationError(
                f"Cozi did not apply the text update: asked for {text!r}, "
                f"server returned {item.text!r}"
            )
        return item

    async def mark_item(self, list_id: str, item_id: str, status: ItemStatus) -> CoziItem:
        """
        Mark an item as complete or incomplete.

        Args:
            list_id: ID of the list containing the item
            item_id: ID of the item to update
            status: New status for the item

        Returns:
            Updated CoziItem object

        Raises:
            ResourceNotFoundError: item_id does not exist (Cozi upserted instead)
            WriteVerificationError: server did not apply the new status
        """
        item = await self._put_item(list_id, item_id, {"status": status.value})
        # use_enum_values=True means item.status is the plain string value.
        if item.status != status.value:
            raise WriteVerificationError(
                f"Cozi did not apply the status update: asked for {status.value!r}, "
                f"server returned {item.status!r}"
            )
        return item


    async def remove_items(self, list_id: str, item_ids: List[str]) -> bool:
        """
        Remove multiple items from a list.
        
        Args:
            list_id: ID of the list containing the items
            item_ids: List of item IDs to remove
        
        Returns:
            True if removal was successful

        Raises:
            WriteVerificationError: one or more items survived the removal
        """
        if not item_ids:
            return True

        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/list/{list_id}"
        operations = [{"op": "remove", "path": f"/items/{item_id}"} for item_id in item_ids]

        response = await self._make_request("PATCH", endpoint, data={"operations": operations})

        # The PATCH response is the full post-state of the list, so the removal can
        # be confirmed without a second round trip. Cozi tolerates removing an id
        # that was never there (200, list unchanged) — that still satisfies "it is
        # not there", so only surviving ids are an error.
        if isinstance(response, dict) and isinstance(response.get("items"), list):
            remaining = {
                item.get("itemId") or item.get("id")
                for item in response["items"]
                if isinstance(item, dict)
            }
            survived = [item_id for item_id in item_ids if item_id in remaining]
            if survived:
                raise WriteVerificationError(
                    f"Cozi did not remove {len(survived)} of {len(item_ids)} item(s) "
                    f"from list {list_id}: {', '.join(survived)}",
                    response_data=response,
                )
        return True
    
    # Calendar Management
    
    async def get_calendar(self, year: int, month: int) -> List[CoziAppointment]:
        """
        Get calendar appointments for a specific month.
        
        Args:
            year: Year (e.g., 2024)
            month: Month (1-12)
        
        Returns:
            List of CoziAppointment objects
        """
        if not (1 <= month <= 12):
            raise ValidationError("Month must be between 1 and 12")
        
        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/calendar/{year}/{month}"
        response = await self._make_request("GET", endpoint)
        
        appointments = []
        if isinstance(response, dict) and "items" in response:
            # New API format: response has 'days' and 'items' keys
            items = response.get("items", {})
            for item_id, item_data in items.items():
                try:
                    # Convert the new format to CoziAppointment
                    appointment = self._parse_calendar_item(item_data)
                    if appointment:
                        appointments.append(appointment)
                except (ValueError, TypeError, KeyError, PydanticValidationError) as e:
                    logger.warning(f"Failed to parse appointment {item_id}: {e}")
        elif isinstance(response, list):
            for appt_data in response:
                try:
                    appointments.append(CoziAppointment.model_validate(appt_data))
                except (ValueError, TypeError, KeyError, PydanticValidationError) as e:
                    logger.warning(f"Failed to parse appointment: {e}")

        return appointments
    
    def _parse_calendar_item(self, item_data: Dict[str, Any]) -> Optional[CoziAppointment]:
        try:
            # Extract basic info
            subject = item_data.get('description', '').strip()
            if not subject:
                subject = item_data.get('descriptionShort', '').strip()
            
            # Parse date
            day_str = item_data.get('day', '')
            try:
                start_day = datetime.strptime(day_str, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                logger.warning(f"Invalid date format: {day_str}")
                return None
            
            # Parse times
            start_time = None
            end_time = None
            
            start_time_str = item_data.get('startTime')
            if start_time_str and start_time_str != '00:00:00':
                try:
                    hour, minute, second = map(int, start_time_str.split(':'))
                    start_time = time(hour=hour, minute=minute)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid start time format: {start_time_str}")
            
            end_time_str = item_data.get('endTime')
            if end_time_str and end_time_str != '00:00:00':
                try:
                    hour, minute, second = map(int, end_time_str.split(':'))
                    end_time = time(hour=hour, minute=minute)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid end time format: {end_time_str}")
            
            # Preserve the full itemDetails dict so CoziAppointment.extract_item_details
            # can hoist nested fields (location, notes, …) into top-level attributes.
            # Previously this synthesized {'location': ...} and silently dropped notes.
            item_details = item_data.get('itemDetails', {})
            if not isinstance(item_details, dict):
                item_details = {}

            attendees = item_data.get('householdMembers', [])
            date_span = item_data.get('dateSpan', 0)

            appointment_data = {
                'id': item_data.get('id'),
                'description': subject,
                'day': start_day.isoformat(),
                'startTime': start_time.strftime('%H:%M:%S') if start_time else None,
                'endTime': end_time.strftime('%H:%M:%S') if end_time else None,
                'dateSpan': date_span,
                'householdMembers': attendees,
                'itemDetails': item_details,
            }
            return CoziAppointment.model_validate(appointment_data)

        except (ValueError, TypeError, KeyError, PydanticValidationError) as e:
            logger.error(f"Error parsing calendar item: {e}")
            return None
    
    async def create_appointment(self, appointment: CoziAppointment) -> CoziAppointment:
        """
        Create a new calendar appointment.

        The Cozi API does not return a dedicated identifier for the new appointment;
        we locate it in the calendar response by matching the start day and subject.
        Two appointments created on the same day with the same subject cannot be
        disambiguated — the first match wins.

        Args:
            appointment: CoziAppointment object to create

        Returns:
            Created CoziAppointment object with id populated

        Raises:
            ValidationError: subject is empty
            APIError: response did not contain the newly created appointment
        """
        if not appointment.subject.strip():
            raise ValidationError("Appointment subject cannot be empty")

        year = appointment.start_day.year
        month = appointment.start_day.month

        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/calendar/{year}/{month}"
        response = await self._make_request(
            "POST",
            endpoint,
            data=[appointment.to_api_create_format()]
        )

        logger.debug(f"Create appointment response: {response}")
        _assert_not_rejected(response, "create")

        if isinstance(response, dict):
            items = response.get('items', {})
            target_date_str = appointment.start_day.isoformat()

            for item_id, item_data in items.items():
                if (item_data.get('day') == target_date_str and
                    item_data.get('description') == appointment.subject):
                    appointment.id = item_id
                    logger.info(f"Found created appointment with ID: {item_id}")
                    return appointment

            for item_id, item_data in items.items():
                if item_data.get('description') == appointment.subject:
                    appointment.id = item_id
                    logger.info(f"Found created appointment by subject match with ID: {item_id}")
                    return appointment

        raise APIError(
            "Created appointment not found in server response",
            response_data=response if isinstance(response, dict) else {"response": response},
        )
    
    async def update_appointment(self, appointment: CoziAppointment) -> CoziAppointment:
        """
        Update an existing calendar appointment.
        
        Args:
            appointment: CoziAppointment object to update (must have ID)
        
        Returns:
            Updated CoziAppointment object

        Raises:
            ValidationError: appointment has no ID
            WriteVerificationError: Cozi discarded the edit
        """
        if not appointment.id:
            raise ValidationError("Cannot update appointment without ID")

        year = appointment.start_day.year
        month = appointment.start_day.month

        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/calendar/{year}/{month}"
        response = await self._make_request(
            "POST",
            endpoint,
            data=[appointment.to_api_edit_format()]
        )

        logger.debug(f"Update appointment response: {response}")
        _assert_not_rejected(response, "edit", appointment.id)

        # Return the updated appointment (API may not return detailed response)
        return appointment
    
    async def delete_appointment(self, appointment_id: str, year: int, month: int) -> bool:
        """
        Delete a calendar appointment.
        
        Args:
            appointment_id: ID of the appointment to delete
            year: Year of the appointment
            month: Month of the appointment
        
        Returns:
            True if deletion was successful

        Raises:
            WriteVerificationError: Cozi refused the delete
        """
        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/calendar/{year}/{month}"
        delete_data = [{
            "itemType": "appointment",
            "delete": {"id": appointment_id}
        }]

        response = await self._make_request("POST", endpoint, data=delete_data)
        # Cozi treats deleting an unknown id as a no-op success (no rejection), so
        # this catches malformed or refused deletes, not "it was already gone".
        _assert_not_rejected(response, "delete", appointment_id)
        return True