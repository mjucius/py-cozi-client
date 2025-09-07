"""
Enhanced Cozi Family Organizer API Client

This module provides a comprehensive, async client for interacting with the Cozi API.
"""

import asyncio
import logging
from datetime import datetime, date, time
from typing import List, Optional, Dict, Any, Union
from urllib.parse import urljoin

import aiohttp

from exceptions import (
    CoziException,
    AuthenticationError,
    RateLimitError,
    APIError,
    NetworkError,
    ResourceNotFoundError,
    ValidationError,
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
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
        require_auth: bool = True,
    ) -> Dict[str, Any]:
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
        await self._ensure_session()
        
        if require_auth and not self._authenticated:
            logger.debug(f"Not authenticated, calling authenticate(). Current account_id: {self._account_id}")
            await self.authenticate()
            logger.debug(f"After authenticate(). New account_id: {self._account_id}")
        
        # Rate limiting
        now = asyncio.get_event_loop().time()
        time_since_last = now - self._last_request_time
        if time_since_last < self._min_request_interval:
            await asyncio.sleep(self._min_request_interval - time_since_last)
        
        url = urljoin(self.BASE_URL, endpoint)
        headers = self._get_auth_headers() if require_auth else {}
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
                self._last_request_time = asyncio.get_event_loop().time()
                
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
                        return response_data
                    
                    # Handle successful responses with no content (204)
                    elif response.status == 204:
                        self._last_response_data = None
                        logger.debug(f"API request successful: {method} {endpoint} (status: {response.status}, no content)")
                        return True  # Return True to indicate successful operation
                    
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
                                # Try re-authenticating once
                                logger.info("Authentication failed, retrying login")
                                self._authenticated = False
                                await self.authenticate()
                                headers = self._get_auth_headers()
                                continue
                            else:
                                raise AuthenticationError(
                                    "Authentication failed",
                                    status_code=response.status,
                                    response_data=response_data
                                )
                        elif response.status == 403:
                            raise ValidationError(
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
                        else:
                            raise APIError(
                                f"API request failed: {response.status}",
                                status_code=response.status,
                                response_data=response_data
                            )
            
            except aiohttp.ClientError as e:
                if attempt < self.retry_attempts - 1:
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
            require_auth=False
        )
        
        logger.debug(f"Authentication response: {response}")
        
        self._access_token = response.get("accessToken")
        self._token_expires = response.get("expiresIn")
        self._account_id = response.get("accountId")
        
        logger.debug(f"Parsed auth data - token: {self._access_token is not None}, account_id: {self._account_id}")
        
        if not all([self._access_token, self._account_id]):
            raise AuthenticationError(f"Invalid login response format. Response: {response}")
        
        self._authenticated = True
        logger.info("Successfully authenticated with Cozi API")
    
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
            return [CoziPerson.from_api_response(person) for person in response]
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
            return [CoziList.from_api_response(list_data) for list_data in response]
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
        return [lst for lst in all_lists if lst.list_type == list_type]
    
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
        
        return CoziList.from_api_response(response)
    
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
        return CoziList.from_api_response(response)
    
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
        
        return CoziItem.from_api_response(response)
    
    async def update_item_text(self, list_id: str, item_id: str, text: str) -> CoziItem:
        """
        Update the text of a list item.
        
        Args:
            list_id: ID of the list containing the item
            item_id: ID of the item to update
            text: New item text
        
        Returns:
            Updated CoziItem object
        """
        if not text.strip():
            raise ValidationError("Item text cannot be empty")
        
        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/list/{list_id}/item/{item_id}"
        response = await self._make_request("PUT", endpoint, data={"text": text})
        
        return CoziItem.from_api_response(response)
    
    async def mark_item(self, list_id: str, item_id: str, status: ItemStatus) -> CoziItem:
        """
        Mark an item as complete or incomplete.
        
        Args:
            list_id: ID of the list containing the item
            item_id: ID of the item to update
            status: New status for the item
        
        Returns:
            Updated CoziItem object
        """
        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/list/{list_id}/item/{item_id}"
        response = await self._make_request("PUT", endpoint, data={"status": status.value})
        
        return CoziItem.from_api_response(response)
    
    async def remove_items(self, list_id: str, item_ids: List[str]) -> bool:
        """
        Remove multiple items from a list.
        
        Args:
            list_id: ID of the list containing the items
            item_ids: List of item IDs to remove
        
        Returns:
            True if removal was successful
        """
        if not item_ids:
            return True
        
        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/list/{list_id}"
        operations = [{"op": "remove", "path": f"/items/{item_id}"} for item_id in item_ids]
        
        await self._make_request("PATCH", endpoint, data={"operations": operations})
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
        if isinstance(response, list):
            for appt_data in response:
                try:
                    appointments.append(CoziAppointment.from_api_response(appt_data))
                except Exception as e:
                    logger.warning(f"Failed to parse appointment: {e}")
        
        return appointments
    
    async def create_appointment(self, appointment: CoziAppointment) -> CoziAppointment:
        """
        Create a new calendar appointment.
        
        Args:
            appointment: CoziAppointment object to create
        
        Returns:
            Created CoziAppointment object
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
        
        # Handle the complex calendar response format
        if isinstance(response, dict):
            # Look for our appointment in the items section
            items = response.get('items', {})
            target_date_str = appointment.start_day.isoformat()
            
            # Find the appointment by checking the items for our date and subject
            for item_id, item_data in items.items():
                if (item_data.get('day') == target_date_str and 
                    item_data.get('description') == appointment.subject):
                    appointment.id = item_id
                    logger.info(f"Found created appointment with ID: {item_id}")
                    return appointment
            
            # If not found by date match, try to find the most recently created item with our subject
            for item_id, item_data in items.items():
                if item_data.get('description') == appointment.subject:
                    appointment.id = item_id
                    logger.info(f"Found created appointment by subject match with ID: {item_id}")
                    return appointment
        
        # If no ID found, log the response for debugging
        logger.warning(f"Could not find created appointment ID in response. Response keys: {list(response.keys()) if isinstance(response, dict) else 'Not a dict'}")
        return appointment
    
    async def update_appointment(self, appointment: CoziAppointment) -> CoziAppointment:
        """
        Update an existing calendar appointment.
        
        Args:
            appointment: CoziAppointment object to update (must have ID)
        
        Returns:
            Updated CoziAppointment object
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
        """
        await self._ensure_authenticated()
        endpoint = f"/api/ext/{self.API_VERSION}/{self._account_id}/calendar/{year}/{month}"
        delete_data = [{
            "itemType": "appointment",
            "delete": {"id": appointment_id}
        }]
        
        await self._make_request("POST", endpoint, data=delete_data)
        return True