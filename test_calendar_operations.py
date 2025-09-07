#!/usr/bin/env python3
"""
Test script for Cozi API calendar operations.

This script demonstrates:
1. Interactive credential collection
2. Authentication with the Cozi API
3. Creating a calendar appointment
4. Updating the appointment time
5. Deleting the appointment

Usage:
    python test_calendar_operations.py
"""

import asyncio
import getpass
import sys
import json
import logging
from datetime import date, time, datetime
from cozi_client import CoziClient
from models import CoziAppointment
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
    print("Cozi API Calendar Test")
    print("=" * 30)
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


def select_attendee(family_members):
    """Prompt user to select which family member should attend the appointment."""
    if not family_members:
        print_info("No family members found, appointment will have no attendees")
        return []
    
    print(f"\nFound {len(family_members)} family members:")
    for i, member in enumerate(family_members, 1):
        print(f"  {i}. {member.name} ({member.email or 'no email'}) [ID: {member.id}]")
    
    print("  0. No attendees (create appointment without any attendees)")
    
    while True:
        try:
            choice = input(f"\nSelect attendee for the test appointment (0-{len(family_members)}): ").strip()
            
            if choice == "0":
                print_info("Creating appointment with no attendees")
                return []
            
            choice_num = int(choice)
            if 1 <= choice_num <= len(family_members):
                selected_member = family_members[choice_num - 1]
                if selected_member.id and selected_member.id.strip():
                    print_info(f"Selected attendee: {selected_member.name} (ID: {selected_member.id})")
                    return [selected_member.id]
                else:
                    print_error(f"Selected member '{selected_member.name}' has no valid ID, using empty attendee list")
                    return []
            else:
                print_error(f"Please enter a number between 0 and {len(family_members)}")
        
        except ValueError:
            print_error("Please enter a valid number")
        except KeyboardInterrupt:
            print_error("\nSelection cancelled, using no attendees")
            return []


async def test_calendar_operations():
    """Test calendar operations: create, update, delete appointment."""
    
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
            
            # Get family members and let user select attendee
            try:
                family_members = await client.get_family_members()
                attendee_ids = select_attendee(family_members)
                
            except Exception as e:
                print_info(f"Could not fetch family members: {e}")
                attendee_ids = []
            
            print_step(2, "Creating test appointment for today at noon")
            
            # Create appointment for today at noon (12:00 PM) for 1 hour
            today = date.today()
            start_time = time(12, 0)  # 12:00 PM
            end_time = time(13, 0)    # 1:00 PM
            
            # Create unique subject with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            subject = f"API Test Meeting {timestamp}"
            
            test_appointment = CoziAppointment(
                id=None,
                subject=subject,
                start_day=today,
                start_time=start_time,
                end_time=end_time,
                date_span=0,
                attendees=attendee_ids,
                location="Test Location",
                notes=f"Created by test script at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            print_info(f"Creating appointment for {today} from {start_time} to {end_time}")
            
            # Show the data we're sending to the API
            api_create_data = test_appointment.to_api_create_format()
            print_json("Request Data Sent to API", api_create_data)
            
            try:
                created_appointment = await client.create_appointment(test_appointment)
                
                # Show the raw API response
                raw_response = client.get_last_response_data()
                if raw_response:
                    print_json("Raw API Response Received", raw_response)
                
                print_success("Appointment created successfully!")
                print(f"  📅 Subject: {created_appointment.subject}")
                print(f"  📆 Date: {created_appointment.start_day}")
                print(f"  🕐 Time: {created_appointment.start_time} - {created_appointment.end_time}")
                print(f"  📍 Location: {created_appointment.location}")
                print(f"  🆔 ID: {created_appointment.id}")
                
                if not created_appointment.id:
                    print_error("Warning: Created appointment has no ID, cannot proceed with update/delete")
                    print_info("This may be because the API response format is different than expected")
                    print_info("Check the 'Raw API Response Received' above to see what was actually returned")
                    return
                
                wait_for_user()
                
            except ValidationError as e:
                print_error(f"Validation error creating appointment: {e}")
                return
            except APIError as e:
                print_error(f"API error creating appointment: {e}")
                
                # Show the raw API response for debugging
                error_response = client.get_last_response_data()
                if error_response:
                    print_json("Error API Response", error_response)
                return
            
            print_step(3, "Updating appointment time to 2:00 PM - 3:00 PM")
            
            # Update the appointment time to 2:00 PM - 3:00 PM
            created_appointment.start_time = time(14, 0)  # 2:00 PM
            created_appointment.end_time = time(15, 0)    # 3:00 PM
            created_appointment.subject = f"{subject} (Updated)"
            created_appointment.notes += "\n\nUpdated by test script"
            
            print_info(f"Updating appointment ID '{created_appointment.id}' to 2:00-3:00 PM")
            
            # Show the update data we're sending
            api_update_data = created_appointment.to_api_edit_format()
            print_json("Update Data Sent to API", api_update_data)
            
            try:
                updated_appointment = await client.update_appointment(created_appointment)
                
                # Show the raw API response for update
                raw_update_response = client.get_last_response_data()
                if raw_update_response:
                    print_json("Raw Update API Response", raw_update_response)
                
                print_success("Appointment updated successfully!")
                print(f"  📅 Subject: {updated_appointment.subject}")
                print(f"  🕐 New Time: {updated_appointment.start_time} - {updated_appointment.end_time}")
                
                wait_for_user()
                
            except ValidationError as e:
                print_error(f"Validation error updating appointment: {e}")
                return
            except APIError as e:
                print_error(f"API error updating appointment: {e}")
                return
            
            print_step(4, "Deleting the test appointment")
            
            print_info(f"Deleting appointment ID '{created_appointment.id}' from {today.year}/{today.month}")
            
            try:
                success = await client.delete_appointment(
                    created_appointment.id,
                    today.year,
                    today.month
                )
                
                # Show the raw API response for delete
                raw_delete_response = client.get_last_response_data()
                if raw_delete_response:
                    print_json("Raw Delete API Response", raw_delete_response)
                
                if success:
                    print_success("Appointment deleted successfully!")
                else:
                    print_error("Failed to delete appointment")
                
                wait_for_user()
                
            except APIError as e:
                print_error(f"API error deleting appointment: {e}")
                return
            
            print_step(5, "Verifying appointment was deleted")
            
            # Verify the appointment was deleted by fetching current month's appointments
            try:
                current_appointments = await client.get_calendar(today.year, today.month)
                test_appointments = [
                    appt for appt in current_appointments 
                    if appt.subject in [subject, f"{subject} (Updated)"]
                ]
                
                if not test_appointments:
                    print_success("Confirmed: Test appointment was successfully removed from calendar")
                else:
                    print_error(f"Warning: Found {len(test_appointments)} test appointments still in calendar")
                    for appt in test_appointments:
                        print(f"  - {appt.subject} at {appt.start_time}")
                
                print_info(f"Total appointments in {today.strftime('%B %Y')}: {len(current_appointments)}")
                
            except APIError as e:
                print_error(f"Could not verify deletion: {e}")
            
            print("\n" + "=" * 50)
            print("✅ Calendar operations test completed successfully!")
            print("✅ All operations (create, update, delete) worked as expected")
            
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
    print("Starting Cozi API Calendar Operations Test")
    print("This test will:")
    print("1. Authenticate with your Cozi account")
    print("2. Create a test appointment for today at noon")
    print("3. Update the appointment time to 2:00 PM")
    print("4. Delete the test appointment")
    print("5. Verify the appointment was removed")
    print()
    
    confirm = input("Continue? (y/N): ").strip().lower()
    if confirm not in ['y', 'yes']:
        print("Test cancelled.")
        return
    
    await test_calendar_operations()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\nFatal error: {e}")
        sys.exit(1)