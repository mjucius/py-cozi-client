"""
Data models for the Cozi API client.
"""

from dataclasses import dataclass
from datetime import datetime, date, time
from enum import Enum
from typing import List, Optional, Dict, Any


class ListType(Enum):
    """Supported list types in Cozi."""
    SHOPPING = "shopping"
    TODO = "todo"


class ItemStatus(Enum):
    """Status options for list items."""
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


@dataclass
class CoziPerson:
    """Represents a family member/person in Cozi account."""
    id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    color: Optional[str] = None  # Color code for calendar events
    
    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> 'CoziPerson':
        """Create CoziPerson from API response data."""
        return cls(
            id=data.get('accountPersonId', ''),
            name=data.get('name', ''),
            email=data.get('email'),
            phone=data.get('phone'),
            color=data.get('colorIndex')  # API uses colorIndex instead of color
        )


@dataclass
class CoziItem:
    """Represents an item in a Cozi list."""
    id: Optional[str]
    text: str
    status: ItemStatus
    position: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> 'CoziItem':
        """Create CoziItem from API response data."""
        return cls(
            id=data.get('itemId') or data.get('id'),
            text=data.get('text', ''),
            status=ItemStatus(data.get('status', 'incomplete')),
            position=data.get('position'),
            created_at=cls._parse_datetime(data.get('createdAt')),
            updated_at=cls._parse_datetime(data.get('updatedAt'))
        )
    
    @staticmethod
    def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
        """Parse datetime string from API response."""
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return None


@dataclass
class CoziList:
    """Represents a Cozi list (shopping or todo)."""
    id: Optional[str]
    title: str
    list_type: ListType
    items: List[CoziItem]
    owner: Optional[str] = None
    version: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> 'CoziList':
        """Create CoziList from API response data."""
        items_data = data.get('items', [])
        items = [CoziItem.from_api_response(item) for item in items_data]
        
        return cls(
            id=data.get('listId') or data.get('id'),
            title=data.get('title', ''),
            list_type=ListType(data.get('listType', 'todo')),
            items=items,
            owner=data.get('owner'),
            version=data.get('version'),
            created_at=cls._parse_datetime(data.get('createdAt')),
            updated_at=cls._parse_datetime(data.get('updatedAt'))
        )
    
    @staticmethod
    def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
        """Parse datetime string from API response."""
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return None


@dataclass
class CoziAppointment:
    """Represents a calendar appointment in Cozi."""
    id: Optional[str]
    subject: str
    start_day: date
    start_time: Optional[time]
    end_time: Optional[time]
    date_span: int  # Number of additional days (0 = same day, 1 = spans to next day, etc.)
    attendees: List[str]  # List of person IDs
    location: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    @property
    def start_date(self) -> date:
        """Alias for start_day for compatibility."""
        return self.start_day
    
    def to_api_create_format(self) -> Dict[str, Any]:
        """Convert to API format for creating appointments."""
        data = {
            "itemType": "appointment",
            "create": {
                "description": self.subject,
                "day": self.start_day.isoformat(),
                "dateSpan": self.date_span,
                "householdMembers": self.attendees if self.attendees else [],
            }
        }
        
        if self.start_time:
            data["create"]["startTime"] = self.start_time.strftime("%H:%M:%S")
        if self.end_time:
            data["create"]["endTime"] = self.end_time.strftime("%H:%M:%S")
        if self.location:
            data["create"]["location"] = self.location
        if self.notes:
            data["create"]["notes"] = self.notes
            
        return data
    
    def to_api_edit_format(self) -> Dict[str, Any]:
        """Convert to API format for editing appointments."""
        data = {
            "itemType": "appointment",
            "edit": {
                "id": self.id,
                "description": self.subject,
                "day": self.start_day.isoformat(),
                "dateSpan": self.date_span,
                "householdMembers": self.attendees if self.attendees else [],
            }
        }
        
        if self.start_time:
            data["edit"]["startTime"] = self.start_time.strftime("%H:%M:%S")
        if self.end_time:
            data["edit"]["endTime"] = self.end_time.strftime("%H:%M:%S")
        if self.location:
            data["edit"]["location"] = self.location
        if self.notes:
            data["edit"]["notes"] = self.notes
            
        return data
    
    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> 'CoziAppointment':
        """Create CoziAppointment from API response data."""
        details = data.get('details', {})
        
        return cls(
            id=data.get('id'),
            subject=details.get('subject', ''),
            start_day=cls._parse_date(data.get('startDay')),
            start_time=cls._parse_time(details.get('startTime')),
            end_time=cls._parse_time(details.get('endTime')),
            date_span=details.get('dateSpan', 0),
            attendees=details.get('attendeeSet', []),
            location=details.get('location'),
            notes=details.get('notes'),
            created_at=cls._parse_datetime(data.get('createdAt')),
            updated_at=cls._parse_datetime(data.get('updatedAt'))
        )
    
    @staticmethod
    def _parse_date(date_str: Optional[str]) -> date:
        """Parse date string from API response."""
        if not date_str:
            return date.today()
        try:
            return datetime.fromisoformat(date_str).date()
        except (ValueError, AttributeError):
            return date.today()
    
    @staticmethod
    def _parse_time(time_str: Optional[str]) -> Optional[time]:
        """Parse time string from API response."""
        if not time_str:
            return None
        try:
            hour, minute = map(int, time_str.split(':'))
            return time(hour=hour, minute=minute)
        except (ValueError, AttributeError):
            return None
    
    @staticmethod
    def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
        """Parse datetime string from API response."""
        if not dt_str:
            return None
        try:
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            return None
    
    def to_api_create_format(self) -> Dict[str, Any]:
        """Convert appointment to API create format."""
        return {
            "itemType": "appointment",
            "create": {
                "startDay": self.start_day.isoformat(),
                "details": {
                    "startTime": self.start_time.strftime("%H:%M") if self.start_time else None,
                    "endTime": self.end_time.strftime("%H:%M") if self.end_time else None,
                    "dateSpan": self.date_span,
                    "attendeeSet": self.attendees,
                    "location": self.location,
                    "notes": self.notes,
                    "subject": self.subject,
                }
            }
        }
    
    def to_api_edit_format(self) -> Dict[str, Any]:
        """Convert appointment to API edit format."""
        if not self.id:
            raise ValueError("Cannot edit appointment without ID")
        
        return {
            "itemType": "appointment",
            "edit": {
                "id": self.id,
                "startDay": self.start_day.isoformat(),
                "details": {
                    "startTime": self.start_time.strftime("%H:%M") if self.start_time else None,
                    "endTime": self.end_time.strftime("%H:%M") if self.end_time else None,
                    "dateSpan": self.date_span,
                    "attendeeSet": self.attendees,
                    "subject": self.subject,
                    "location": self.location,
                    "notes": self.notes,
                }
            }
        }
    
    def to_api_delete_format(self) -> Dict[str, Any]:
        """Convert appointment to API delete format."""
        if not self.id:
            raise ValueError("Cannot delete appointment without ID")
        
        return {
            "itemType": "appointment",
            "delete": {
                "id": self.id
            }
        }