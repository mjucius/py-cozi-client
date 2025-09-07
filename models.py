"""
Data models for the Cozi API client.
"""

from pydantic import BaseModel, Field, validator, root_validator
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


class CoziPerson(BaseModel):
    """Represents a family member/person in Cozi account."""
    id: str = Field(alias='accountPersonId')
    name: str = ''
    email: Optional[str] = None
    phone: Optional[str] = Field(None, alias='phoneNumberKey')
    color: Optional[int] = Field(None, alias='colorIndex')
    email_status: Optional[str] = Field(None, alias='emailStatus')
    is_adult: Optional[bool] = Field(None, alias='isAdult')
    account_person_type: Optional[str] = Field(None, alias='accountPersonType')
    account_creator: Optional[bool] = Field(None, alias='accountCreator')
    notifiable: Optional[bool] = None
    version: Optional[int] = None
    phone_number_key: Optional[str] = Field(None, alias='phoneNumberKey')
    settings: Optional[Dict[str, Any]] = None
    notifiable_features: Optional[List[str]] = Field(None, alias='notifiableFeatures')
    
    class Config:
        populate_by_name = True


class CoziItem(BaseModel):
    """Represents an item in a Cozi list."""
    id: Optional[str] = Field(None, alias='itemId')
    text: str = ''
    status: ItemStatus = ItemStatus.INCOMPLETE
    position: Optional[int] = None
    item_type: Optional[str] = Field(None, alias='itemType')
    due_date: Optional[date] = Field(None, alias='dueDate')
    notes: Optional[str] = None
    owner: Optional[str] = None
    version: Optional[int] = None
    created_at: Optional[datetime] = Field(None, alias='createdAt')
    updated_at: Optional[datetime] = Field(None, alias='updatedAt')
    
    class Config:
        populate_by_name = True
        use_enum_values = True
    
    @validator('status', pre=True)
    def parse_status(cls, v):
        if isinstance(v, str):
            return ItemStatus(v)
        return v
    
    @validator('due_date', pre=True)
    def parse_due_date(cls, v):
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v).date()
            except (ValueError, AttributeError):
                return None
        return v
    
    @validator('created_at', 'updated_at', pre=True)
    def parse_datetime(cls, v):
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                return None
        return v
    
    @root_validator(pre=True)
    def handle_id_field(cls, values):
        if 'id' in values and not values.get('itemId'):
            values['itemId'] = values['id']
        return values


class CoziList(BaseModel):
    """Represents a Cozi list (shopping or todo)."""
    id: Optional[str] = Field(None, alias='listId')
    title: str = ''
    list_type: ListType = Field(ListType.TODO, alias='listType')
    items: List[CoziItem] = Field(default_factory=list)
    owner: Optional[str] = None
    version: Optional[int] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = Field(None, alias='createdAt')
    updated_at: Optional[datetime] = Field(None, alias='updatedAt')
    
    class Config:
        populate_by_name = True
        use_enum_values = True
    
    @validator('list_type', pre=True)
    def parse_list_type(cls, v):
        if isinstance(v, str):
            return ListType(v)
        return v
    
    @validator('items', pre=True)
    def parse_items(cls, v):
        if isinstance(v, list):
            return [CoziItem.model_validate(item) if isinstance(item, dict) else item for item in v]
        return v
    
    @validator('created_at', 'updated_at', pre=True)
    def parse_datetime(cls, v):
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                return None
        return v
    
    @root_validator(pre=True)
    def handle_id_field(cls, values):
        if 'id' in values and not values.get('listId'):
            values['listId'] = values['id']
        return values


class CoziAppointment(BaseModel):
    """Represents a calendar appointment in Cozi."""
    id: Optional[str] = None
    subject: str = Field(alias='description')
    start_day: date = Field(alias='day')
    start_time: Optional[time] = Field(None, alias='startTime')
    end_time: Optional[time] = Field(None, alias='endTime')
    date_span: int = Field(default=0, alias='dateSpan')
    attendees: List[str] = Field(default_factory=list, alias='householdMembers')
    location: Optional[str] = None
    notes: Optional[str] = None
    notes_html: Optional[str] = Field(None, alias='notesHtml')
    notes_plain: Optional[str] = Field(None, alias='notesPlain')
    item_type: Optional[str] = Field(None, alias='itemType')
    item_version: Optional[int] = Field(None, alias='itemVersion')
    description_short: Optional[str] = Field(None, alias='descriptionShort')
    recurrence: Optional[Dict[str, Any]] = None
    recurrence_start_day: Optional[str] = Field(None, alias='recurrenceStartDay')
    end_day: Optional[str] = Field(None, alias='endDay')
    read_only: Optional[bool] = Field(None, alias='readOnly')
    item_source: Optional[str] = Field(None, alias='itemSource')
    household_member: Optional[str] = Field(None, alias='householdMember')
    name: Optional[str] = None
    birth_year: Optional[int] = Field(None, alias='birthYear')
    created_at: Optional[datetime] = Field(None, alias='createdAt')
    updated_at: Optional[datetime] = Field(None, alias='updatedAt')
    
    class Config:
        populate_by_name = True
        use_enum_values = True
        json_encoders = {
            datetime: lambda v: v.isoformat(),
            date: lambda v: v.isoformat(),
            time: lambda v: v.strftime("%H:%M:%S")
        }
    
    @validator('start_day', 'end_day', pre=True)
    def parse_date(cls, v):
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v).date()
            except ValueError:
                return date.today()
        return v or date.today()
    
    @validator('start_time', 'end_time', pre=True)
    def parse_time(cls, v):
        if isinstance(v, str):
            try:
                time_parts = v.split(':')
                hour = int(time_parts[0])
                minute = int(time_parts[1])
                second = int(time_parts[2]) if len(time_parts) > 2 else 0
                return time(hour=hour, minute=minute, second=second)
            except (ValueError, IndexError):
                return None
        return v
    
    @validator('created_at', 'updated_at', pre=True)
    def parse_datetime(cls, v):
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace('Z', '+00:00'))
            except ValueError:
                return None
        return v
    
    @root_validator(pre=True)
    def extract_item_details(cls, values):
        """Extract nested itemDetails fields to top level."""
        item_details = values.get('itemDetails', {})
        if item_details:
            for key, value in item_details.items():
                if key not in values or values[key] is None:
                    values[key] = value
        
        if not values.get('description') and values.get('descriptionShort'):
            values['description'] = values['descriptionShort']
            
        return values
    
    @property
    def start_date(self) -> date:
        """Alias for start_day for compatibility."""
        return self.start_day
    
    def to_api_create_format(self) -> Dict[str, Any]:
        """Convert to API format for creating appointments."""
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
        """Convert to API format for editing appointments."""
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