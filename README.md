# py-cozi-client

An unofficial Python client for the Cozi Family Organizer API that provides a robust and type-safe interface to the Cozi service.

## Features

- **Async/await support** - Built with `aiohttp` for efficient async operations
- **Type safety** - Full type hints and Pydantic models for all API interactions
- **Comprehensive API coverage** - Support for lists, calendar, and account management
- **Error handling** - Custom exception classes for different error scenarios
- **Rate limiting** - Built-in rate limit handling and retry logic
- **Authentication** - Secure credential management and session handling

## Installation

```bash
pip install py-cozi-client
```

For development:

```bash
pip install py-cozi-client[dev]
```

## Quick Start

```python
import asyncio
from cozi_client import CoziClient
from models import ListType, ItemStatus

async def main():
    async with CoziClient("your_username", "your_password") as client:
        # Create a shopping list
        shopping_list = await client.create_list("Groceries", ListType.SHOPPING)
        
        # Add items to the list
        await client.add_item(shopping_list.id, "Milk")
        await client.add_item(shopping_list.id, "Bread")
        
        # Get all lists
        lists = await client.get_lists()
        for lst in lists:
            print(f"List: {lst.title} ({lst.list_type})")

asyncio.run(main())
```

## API Reference

### CoziClient

The main client class for interacting with the Cozi API.

#### Authentication
```python
# Authentication happens automatically with username/password in constructor
client = CoziClient(username, password)

# Or logout manually
await client.logout()
```

#### List Management
```python
# Create lists
await client.create_list(title: str, list_type: ListType) -> CoziList

# Get lists
await client.get_lists() -> List[CoziList]
await client.get_lists_by_type(list_type: ListType) -> List[CoziList]

# Update lists
await client.update_list(list_obj: CoziList) -> CoziList

# Delete lists
await client.delete_list(list_id: str) -> bool
```

#### Item Management
```python
# Add items
await client.add_item(list_id: str, text: str, position: int = 0) -> CoziItem

# Update item text
await client.update_item_text(list_id: str, item_id: str, text: str) -> CoziItem

# Mark item status
await client.mark_item(list_id: str, item_id: str, status: ItemStatus) -> CoziItem

# Remove items
await client.remove_items(list_id: str, item_ids: List[str]) -> bool
```

#### Calendar Operations
```python
# Get calendar for a specific month
await client.get_calendar(year: int, month: int) -> List[CoziAppointment]

# Create appointments
await client.create_appointment(appointment: CoziAppointment) -> CoziAppointment

# Update appointments
await client.update_appointment(appointment: CoziAppointment) -> CoziAppointment

# Delete appointments
await client.delete_appointment(appointment_id: str, year: int, month: int) -> bool
```

#### Account Management
```python
# Get family members
await client.get_family_members() -> List[CoziPerson]

# Get account information
await client.get_account_info()
```

### Data Models

All models are built with Pydantic for automatic validation, serialization, and type safety.

#### CoziList
```python
class CoziList(BaseModel):
    id: Optional[str]
    title: str
    list_type: ListType  # Automatically converted to string values
    items: List[CoziItem]
    owner: Optional[str] = None
    version: Optional[int] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
```

#### CoziItem
```python
class CoziItem(BaseModel):
    id: Optional[str]
    text: str
    status: ItemStatus  # Automatically converted to string values
    position: Optional[int] = None
    item_type: Optional[str] = None
    due_date: Optional[date] = None
    notes: Optional[str] = None
    owner: Optional[str] = None
    version: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
```

#### CoziAppointment
```python
class CoziAppointment(BaseModel):
    id: Optional[str]
    subject: str
    start_day: date
    start_time: Optional[time]
    end_time: Optional[time]
    date_span: int = 0
    attendees: List[str] = []
    location: Optional[str] = None
    notes: Optional[str] = None
    # ... additional fields for recurrence, item details, etc.
```

#### CoziPerson
```python
class CoziPerson(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    color: Optional[int] = None
    # ... additional account fields
```

### Enums

```python
class ListType(Enum):
    SHOPPING = "shopping"
    TODO = "todo"

class ItemStatus(Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
```

### Exceptions

- `CoziException` - Base exception class
- `AuthenticationError` - Authentication failures
- `ValidationError` - Request validation errors
- `RateLimitError` - API rate limit exceeded
- `APIError` - General API errors
- `NetworkError` - Network connectivity issues
- `ResourceNotFoundError` - Resource not found (404)

## Development

### Setup
```bash
git clone <repository-url>
cd py-cozi-client
pip install -e .[dev]
```

## Examples

See the test files for comprehensive usage examples:
- `test_list_operations.py` - List and item management
- `test_calendar_operations.py` - Calendar and appointment management

## Requirements

- Python 3.7+
- aiohttp 3.9.2+
- pydantic 1.10.0+

## License

MIT License

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Author

Matthew Jucius