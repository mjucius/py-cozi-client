#!/usr/bin/env python3
"""
Test script for Cozi API list operations.

This script demonstrates:
1. Interactive credential collection
2. Authentication with the Cozi API
3. Creating a shopping list and todo list
4. Adding items to the lists
5. Updating item text and status
6. Removing items from lists
7. Deleting the test lists

Usage:
    python test_list_operations.py
"""

import asyncio
import getpass
import sys
import json
import logging
from datetime import datetime
from cozi_client import CoziClient
from models import CoziList, CoziItem, ListType, ItemStatus
from exceptions import (
    AuthenticationError,
    APIError,
    ValidationError,
    NetworkError
)

# Set up logging (INFO level to see important events but reduce noise)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


def get_credentials():
    """Prompt user for Cozi credentials securely."""
    print("Cozi API List Operations Test")
    print("=" * 35)
    print("Please enter your Cozi credentials:")
    
    username = input("Username/Email: ").strip()
    if not username:
        print("Error: Username cannot be empty")
        sys.exit(1)
    
    password = getpass.getpass("Password: ")
    if not password:
        print("Error: Password cannot be empty")
        sys.exit(1)
    
    return username, password


def print_step(step_number, description):
    """Print a formatted step description."""
    print(f"\n--- Step {step_number}: {description} ---")


def print_success(message):
    """Print a success message."""
    print(f"✅ {message}")


def print_error(message):
    """Print an error message."""
    print(f"❌ {message}")


def print_info(message):
    """Print an info message."""
    print(f"ℹ️  {message}")


def wait_for_user():
    """Wait for user to press Enter before continuing."""
    try:
        input("\nPress Enter to continue...")
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user.")
        sys.exit(0)


def print_json(title, data):
    """Print JSON data in a formatted way without truncation."""
    print(f"\n📋 {title}:")
    print("-" * 50)
    try:
        json_str = json.dumps(data, indent=2, default=str)
        print(json_str)
    except Exception as e:
        print(f"Error formatting JSON: {e}")
        print(str(data))
    print("-" * 50)


def print_list_details(cozi_list):
    """Print detailed information about a list."""
    print(f"  📝 Title: {cozi_list.title}")
    print(f"  🆔 ID: {cozi_list.id}")
    print(f"  📋 Type: {cozi_list.list_type.value}")
    print(f"  📦 Items: {len(cozi_list.items)}")
    
    if cozi_list.items:
        print(f"  📄 Items:")
        for i, item in enumerate(cozi_list.items, 1):
            status_icon = "✅" if item.status == ItemStatus.COMPLETE else "⬜"
            print(f"    {i}. {status_icon} {item.text} [ID: {item.id}]")


async def test_list_operations():
    """Test list operations: create, add items, update, mark, remove, delete."""
    
    # Get credentials from user
    username, password = get_credentials()
    
    try:
        print_step(1, "Connecting to Cozi API and authenticating")
        print_info(f"Attempting to authenticate as: {username}")
        
        async with CoziClient(username, password) as client:
            # Show authentication request/response
            auth_request = client.get_last_request_data()
            if auth_request:
                # Safe copy without password
                safe_auth_request = auth_request.copy()
                if safe_auth_request.get('data') and 'password' in safe_auth_request['data']:
                    safe_auth_request['data'] = {**safe_auth_request['data'], 'password': '[HIDDEN]'}
                print_json("Authentication Request (password hidden)", safe_auth_request)
            
            auth_response = client.get_last_response_data()
            if auth_response:
                print_json("Authentication Response", auth_response)
            
            print_success("Connected to Cozi API successfully!")
            wait_for_user()
            
            # Track created lists for cleanup
            created_lists = []
            
            print_step(2, "Creating test shopping list")
            
            # Create unique list titles with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            shopping_title = f"Test Shopping List {timestamp}"
            
            try:
                shopping_list = await client.create_list(shopping_title, ListType.SHOPPING)
                created_lists.append(shopping_list)
                
                # Show the raw API response
                raw_response = client.get_last_response_data()
                if raw_response:
                    print_json("Create Shopping List API Response", raw_response)
                
                print_success("Shopping list created successfully!")
                print_list_details(shopping_list)
                
                wait_for_user()
                
            except ValidationError as e:
                print_error(f"Validation error creating shopping list: {e}")
                return
            except APIError as e:
                print_error(f"API error creating shopping list: {e}")
                return
            
            print_step(3, "Creating test todo list")
            
            todo_title = f"Test Todo List {timestamp}"
            
            try:
                todo_list = await client.create_list(todo_title, ListType.TODO)
                created_lists.append(todo_list)
                
                print_success("Todo list created successfully!")
                print_list_details(todo_list)
                
                wait_for_user()
                
            except ValidationError as e:
                print_error(f"Validation error creating todo list: {e}")
                return
            except APIError as e:
                print_error(f"API error creating todo list: {e}")
                return
            
            print_step(4, "Adding items to shopping list")
            
            shopping_items = [
                "Milk",
                "Bread", 
                "Eggs",
                "Apples",
                "Cheese"
            ]
            
            added_shopping_items = []
            
            for i, item_text in enumerate(shopping_items):
                try:
                    print_info(f"Adding '{item_text}' to shopping list")
                    item = await client.add_item(shopping_list.id, item_text, position=i)
                    
                    # Show the raw API response for debugging
                    raw_response = client.get_last_response_data()
                    if raw_response:
                        print_json(f"Add Item '{item_text}' API Response", raw_response)
                    
                    added_shopping_items.append(item)
                    
                    print_success(f"Added '{item.text}' (ID: {item.id})")
                    
                except APIError as e:
                    print_error(f"Failed to add '{item_text}': {e}")
            
            wait_for_user()
            
            print_step(5, "Adding items to todo list")
            
            todo_items = [
                "Call dentist",
                "Pay electricity bill",
                "Walk the dog",
                "Buy birthday gift",
                "Schedule meeting"
            ]
            
            added_todo_items = []
            
            for i, item_text in enumerate(todo_items):
                try:
                    print_info(f"Adding '{item_text}' to todo list")
                    item = await client.add_item(todo_list.id, item_text, position=i)
                    added_todo_items.append(item)
                    
                    print_success(f"Added '{item.text}' (ID: {item.id})")
                    
                except APIError as e:
                    print_error(f"Failed to add '{item_text}': {e}")
            
            wait_for_user()
            
            print_step(6, "Updating item text")
            
            if added_shopping_items:
                # Update the first shopping item
                first_item = added_shopping_items[0]
                new_text = f"{first_item.text} (Organic)"
                
                try:
                    print_info(f"Updating '{first_item.text}' to '{new_text}'")
                    updated_item = await client.update_item_text(shopping_list.id, first_item.id, new_text)
                    
                    print_success(f"Updated item text to '{updated_item.text}'")
                    
                except APIError as e:
                    print_error(f"Failed to update item text: {e}")
            
            wait_for_user()
            
            print_step(7, "Marking items as complete")
            
            # Mark some shopping items as complete
            items_to_complete = added_shopping_items[:2]  # First 2 items
            
            for item in items_to_complete:
                try:
                    print_info(f"Marking '{item.text}' as complete")
                    completed_item = await client.mark_item(shopping_list.id, item.id, ItemStatus.COMPLETE)
                    
                    print_success(f"Marked '{completed_item.text}' as complete")
                    
                except APIError as e:
                    print_error(f"Failed to mark '{item.text}' as complete: {e}")
            
            # Mark some todo items as complete
            todo_items_to_complete = added_todo_items[:1]  # First item
            
            for item in todo_items_to_complete:
                try:
                    print_info(f"Marking '{item.text}' as complete")
                    completed_item = await client.mark_item(todo_list.id, item.id, ItemStatus.COMPLETE)
                    
                    print_success(f"Marked '{completed_item.text}' as complete")
                    
                except APIError as e:
                    print_error(f"Failed to mark '{item.text}' as complete: {e}")
            
            wait_for_user()
            
            print_step(8, "Removing items from lists")
            
            # Remove the last 2 items from shopping list
            items_to_remove = added_shopping_items[-2:]
            item_ids_to_remove = [item.id for item in items_to_remove]
            
            try:
                print_info(f"Removing {len(item_ids_to_remove)} items from shopping list")
                success = await client.remove_items(shopping_list.id, item_ids_to_remove)
                
                if success:
                    print_success(f"Successfully removed {len(item_ids_to_remove)} items from shopping list")
                else:
                    print_error("Failed to remove items from shopping list")
                
            except APIError as e:
                print_error(f"API error removing items: {e}")
            
            wait_for_user()
            
            print_step(9, "Fetching updated lists to verify changes")
            
            try:
                all_lists = await client.get_lists()
                test_lists = [lst for lst in all_lists if lst.title in [shopping_title, todo_title]]
                
                print_info(f"Found {len(test_lists)} test lists")
                
                for lst in test_lists:
                    print_success(f"List: {lst.title}")
                    print_list_details(lst)
                    print()
                
            except APIError as e:
                print_error(f"Failed to fetch updated lists: {e}")
            
            wait_for_user()
            
            print_step(10, "Testing list filtering by type")
            
            try:
                shopping_lists = await client.get_lists_by_type(ListType.SHOPPING)
                todo_lists = await client.get_lists_by_type(ListType.TODO)
                
                print_info(f"Found {len(shopping_lists)} shopping lists")
                print_info(f"Found {len(todo_lists)} todo lists")
                
                # Show our test lists
                test_shopping = [lst for lst in shopping_lists if lst.title == shopping_title]
                test_todo = [lst for lst in todo_lists if lst.title == todo_title]
                
                if test_shopping:
                    print_success(f"Found test shopping list: {test_shopping[0].title}")
                if test_todo:
                    print_success(f"Found test todo list: {test_todo[0].title}")
                
            except APIError as e:
                print_error(f"Failed to filter lists by type: {e}")
            
            wait_for_user()
            
            print_step(11, "Deleting test lists")
            
            for lst in created_lists:
                try:
                    print_info(f"Deleting list '{lst.title}' (ID: {lst.id})")
                    success = await client.delete_list(lst.id)
                    
                    if success:
                        print_success(f"Successfully deleted list '{lst.title}'")
                    else:
                        print_error(f"Failed to delete list '{lst.title}'")
                    
                    # Show the raw API response
                    raw_delete_response = client.get_last_response_data()
                    if raw_delete_response:
                        print_json(f"Delete List '{lst.title}' API Response", raw_delete_response)
                    
                except APIError as e:
                    print_error(f"API error deleting list '{lst.title}': {e}")
            
            wait_for_user()
            
            print_step(12, "Verifying lists were deleted")
            
            try:
                all_lists = await client.get_lists()
                remaining_test_lists = [lst for lst in all_lists if lst.title in [shopping_title, todo_title]]
                
                if not remaining_test_lists:
                    print_success("Confirmed: All test lists were successfully deleted")
                else:
                    print_error(f"Warning: Found {len(remaining_test_lists)} test lists still existing")
                    for lst in remaining_test_lists:
                        print(f"  - {lst.title} (ID: {lst.id})")
                
                print_info(f"Total remaining lists: {len(all_lists)}")
                
            except APIError as e:
                print_error(f"Could not verify deletion: {e}")
            
            print("\n" + "=" * 60)
            print("✅ List operations test completed successfully!")
            print("✅ All operations (create, add items, update, mark, remove, delete) worked as expected")
            
    except AuthenticationError:
        print_error("Authentication failed. Please check your username and password.")
        sys.exit(1)
        
    except NetworkError as e:
        print_error(f"Network error: {e}")
        print("Please check your internet connection and try again.")
        sys.exit(1)
        
    except Exception as e:
        print_error(f"Unexpected error: {type(e).__name__}: {e}")
        sys.exit(1)


async def main():
    """Main entry point."""
    print("Starting Cozi API List Operations Test")
    print("This test will:")
    print("1. Authenticate with your Cozi account")
    print("2. Create a test shopping list and todo list")
    print("3. Add multiple items to both lists")
    print("4. Update item text")
    print("5. Mark items as complete")
    print("6. Remove some items from lists")
    print("7. Test list filtering by type")
    print("8. Delete the test lists")
    print("9. Verify the lists were removed")
    print()
    
    confirm = input("Continue? (y/N): ").strip().lower()
    if confirm not in ['y', 'yes']:
        print("Test cancelled.")
        return
    
    await test_list_operations()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\nFatal error: {e}")
        sys.exit(1)