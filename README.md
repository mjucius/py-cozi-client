# py-cozi-client

A Python client for the Cozi Family Organizer API that provides a robust and type-safe interface to the Cozi service.

## Features

- **Async/await support** - Built with `aiohttp` for efficient async operations
- **Type safety** - Full type hints and data classes for all API interactions
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
    async with CoziClient() as client:
        # Authenticate
        await client.authenticate("your_username", "your_password")
        
        # Create a shopping list
        shopping_list = await client.create_list("Groceries", ListType.SHOPPING)
        
        # Add items to the list
        await client.add_item(shopping_list.id, "Milk")
        await client.add_item(shopping_list.id, "Bread")
        
        # Get all lists
        lists = await client.get_lists()
        for lst in lists:
            print(f"List: {lst.name} ({lst.type.value})")

asyncio.run(main())
```

## API Reference

### CoziClient

The main client class for interacting with the Cozi API.

#### Authentication
```python
await client.authenticate(username: str, password: str)
await client.logout()
```

#### List Management
```python
# Create lists
await client.create_list(name: str, list_type: ListType)

# Get lists
await client.get_lists() -> List[CoziList]
await client.get_list(list_id: str) -> CoziList

# Delete lists
await client.delete_list(list_id: str)
```

#### Item Management
```python
# Add items
await client.add_item(list_id: str, text: str, assignee_id: Optional[str] = None)

# Update items
await client.update_item(list_id: str, item_id: str, text: Optional[str] = None, 
                        status: Optional[ItemStatus] = None)

# Remove items
await client.remove_item(list_id: str, item_id: str)

# Get items
await client.get_items(list_id: str) -> List[CoziItem]
```

#### Calendar Operations
```python
# Get appointments
await client.get_appointments(start_date: date, end_date: date) -> List[CoziAppointment]

# Create appointments
await client.create_appointment(subject: str, start_time: datetime, 
                               end_time: datetime, location: Optional[str] = None)
```

#### Account Management
```python
# Get family members
await client.get_family_members() -> List[CoziPerson]

# Get account information
await client.get_account_info()
```

### Data Models

#### CoziList
```python
@dataclass
class CoziList:
    id: str
    name: str
    type: ListType
    created_date: datetime
    items: List[CoziItem]
```

#### CoziItem
```python
@dataclass
class CoziItem:
    id: str
    text: str
    status: ItemStatus
    assignee_id: Optional[str]
    created_date: datetime
```

#### CoziAppointment
```python
@dataclass
class CoziAppointment:
    id: str
    subject: str
    start_time: datetime
    end_time: datetime
    location: Optional[str]
    attendees: List[str]
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

## License

MIT License

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Author

Matthew Jucius