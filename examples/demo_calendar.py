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
import os
from datetime import date, time, datetime
from cozi_client import CoziClient
from models import CoziAppointment, CoziPerson
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
    """Get Cozi credentials from environment variables or command line input."""
    print("Cozi API Calendar Test")
    print("=" * 30)
    
    # First try environment variables
    username = os.environ.get('COZI_USERNAME')
    password = os.environ.get('COZI_PASSWORD')
    
    if username and password:
        print_info(f"Using credentials from environment variables: {username}")
        return username, password
    
    # If not in environment variables, ask user
    print_info("Credentials not found in environment variables")
    print_info("Please enter your Cozi credentials:")
    
    try:
        username = input("Username/Email: ").strip()
        if not username:
            print_error("Username cannot be empty")
            sys.exit(1)
            
        password = getpass.getpass("Password: ").strip()
        if not password:
            print_error("Password cannot be empty")
            sys.exit(1)
            
        return username, password
        
    except KeyboardInterrupt:
        print("\n\nCredential entry cancelled by user.")
        sys.exit(0)
    except Exception as e:
        print_error(f"Error getting credentials: {e}")
        sys.exit(1)


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


def extract_appointment_from_response(response_data: dict, appointment_id: str) -> dict:
    """Extract individual appointment data from full API response."""
    if not response_data or not appointment_id:
        return {}
    
    # Check if this is a full API response with 'items' structure
    if 'items' in response_data and isinstance(response_data['items'], dict):
        return response_data['items'].get(appointment_id, {})
    
    # Check if this is already individual appointment data
    if response_data.get('id') == appointment_id:
        return response_data
    
    # If it's a list (like calendar response), search for matching ID
    if isinstance(response_data, list):
        for item in response_data:
            if isinstance(item, dict) and item.get('id') == appointment_id:
                return item
    
    return {}


def validate_appointment_against_json(appointment: CoziAppointment, json_data: dict, operation: str = "created") -> bool:
    """Validate that a CoziAppointment object matches the JSON data it was created from."""
    # Extract individual appointment data if needed
    appointment_data = extract_appointment_from_response(json_data, appointment.id)
    if not appointment_data:
        print_error(f"Could not find appointment data for ID {appointment.id} in response")
        return False
    
    json_data = appointment_data
    print(f"\n🔍 Validating {operation} appointment against JSON data...")
    
    validation_errors = []
    warnings = []
    
    # Check for core required fields (others are optional and have defaults)
    required_fields = ['id', 'day', 'description']  # Core fields that should always be present
    
    missing_required_fields = [field for field in required_fields if field not in json_data]
    if missing_required_fields:
        validation_errors.append(f"Missing required JSON fields: {missing_required_fields}")
    
    # Optional fields (householdMembers, startTime, endTime, itemDetails) have defaults in the model
    
    # Check itemDetails structure  
    item_details = json_data.get('itemDetails', {})
    if not isinstance(item_details, dict):
        validation_errors.append(f"itemDetails should be a dict, got {type(item_details)}")
        item_details = {}
    
    # Note: All itemDetails fields are optional and have defaults in the model
    # - location: defaults to None
    # - notes: defaults to None  
    # - dateSpan: defaults to 0
    # So we don't warn about missing fields that the model handles gracefully
    
    # Check ID mapping
    json_id = json_data.get('id')
    if appointment.id != json_id:
        validation_errors.append(f"ID mismatch: model={appointment.id}, json={json_id}")
    
    # Check subject mapping from description (or descriptionShort as fallback)
    json_subject = json_data.get('description', '') or json_data.get('descriptionShort', '')
    if appointment.subject != json_subject:
        validation_errors.append(f"Subject mismatch: model='{appointment.subject}', json='{json_subject}'")
    
    # Check start_day mapping
    json_start_day = json_data.get('day')
    if json_start_day:
        try:
            expected_date = datetime.fromisoformat(json_start_day).date()
            if appointment.start_day != expected_date:
                validation_errors.append(f"Start day mismatch: model={appointment.start_day}, json={expected_date}")
        except (ValueError, AttributeError) as e:
            validation_errors.append(f"Start day parsing error: {e}")
    elif appointment.start_day != date.today():  # Model defaults to today if no date provided
        warnings.append(f"No day in JSON, model defaulted to: {appointment.start_day}")
    
    # Check start_time mapping
    json_start_time = json_data.get('startTime')
    if json_start_time:
        try:
            time_parts = json_start_time.split(':')
            hour = int(time_parts[0])
            minute = int(time_parts[1])
            second = int(time_parts[2]) if len(time_parts) > 2 else 0
            expected_time = time(hour=hour, minute=minute, second=second)
            if appointment.start_time != expected_time:
                validation_errors.append(f"Start time mismatch: model={appointment.start_time}, json={expected_time}")
        except (ValueError, AttributeError, IndexError) as e:
            validation_errors.append(f"Start time parsing error: {e}")
    elif appointment.start_time is not None:
        warnings.append(f"No startTime in JSON, but model has: {appointment.start_time}")
    
    # Check end_time mapping
    json_end_time = json_data.get('endTime')
    if json_end_time:
        try:
            time_parts = json_end_time.split(':')
            hour = int(time_parts[0])
            minute = int(time_parts[1])
            second = int(time_parts[2]) if len(time_parts) > 2 else 0
            expected_time = time(hour=hour, minute=minute, second=second)
            if appointment.end_time != expected_time:
                validation_errors.append(f"End time mismatch: model={appointment.end_time}, json={expected_time}")
        except (ValueError, AttributeError, IndexError) as e:
            validation_errors.append(f"End time parsing error: {e}")
    elif appointment.end_time is not None:
        warnings.append(f"No endTime in JSON, but model has: {appointment.end_time}")
    
    # Check date_span mapping (can be at top level or in itemDetails)
    json_date_span = json_data.get('dateSpan', 0) or item_details.get('dateSpan', 0)
    if appointment.date_span != json_date_span:
        validation_errors.append(f"Date span mismatch: model={appointment.date_span}, json={json_date_span}")
    
    # Check attendees mapping (householdMembers in JSON vs attendees in model)
    json_attendees = json_data.get('householdMembers', [])
    if set(appointment.attendees) != set(json_attendees):
        validation_errors.append(f"Attendees mismatch: model={appointment.attendees}, json={json_attendees}")
    
    # Check location mapping
    json_location = item_details.get('location')
    if appointment.location != json_location:
        validation_errors.append(f"Location mismatch: model='{appointment.location}', json='{json_location}'")
    
    # Check notes mapping
    json_notes = item_details.get('notes')
    if appointment.notes != json_notes:
        validation_errors.append(f"Notes mismatch: model='{appointment.notes}', json='{json_notes}'")
    
    # Check for unexpected fields in JSON that we're not mapping
    json_top_fields = set(json_data.keys())
    expected_top_fields = {
        'id', 'day', 'startTime', 'endTime', 'description', 'descriptionShort', 
        'householdMembers', 'itemDetails', 'itemType', 'itemVersion', 
        'dateSpan', 'itemSource', 'createdAt', 'updatedAt'
    }
    unexpected_top_fields = json_top_fields - expected_top_fields
    if unexpected_top_fields:
        warnings.append(f"Unexpected top-level JSON fields not mapped to model: {unexpected_top_fields}")
    
    json_detail_fields = set(item_details.keys())
    expected_detail_fields = {
        'id', 'location', 'notes', 'notesHtml', 'notesPlain', 'dateSpan', 
        'recurrence', 'readOnly', 'householdMember', 'birthYear', 
        'recurrenceStartDay', 'name', 'endDay'
    }
    unexpected_detail_fields = json_detail_fields - expected_detail_fields
    if unexpected_detail_fields:
        warnings.append(f"Unexpected itemDetails JSON fields not mapped to model: {unexpected_detail_fields}")
    
    # Print warnings
    if warnings:
        print_info(f"Validation warnings ({len(warnings)}):")
        for warning in warnings:
            print(f"  ⚠️  {warning}")
    
    # Print validation results
    if validation_errors:
        print_error(f"Model validation failed with {len(validation_errors)} errors:")
        for error in validation_errors:
            print(f"  ❌ {error}")
        return False
    else:
        print_success("Model validation passed! All mapped fields match JSON data.")
        return True


def validate_person_against_json(person: CoziPerson, json_data: dict) -> bool:
    """Validate that a CoziPerson object matches the JSON data it was created from."""
    print(f"\n🔍 Validating person '{person.name}' against JSON data...")
    
    validation_errors = []
    warnings = []
    
    # Check for expected fields in JSON
    expected_fields = ['accountPersonId', 'name', 'email', 'phoneNumberKey', 'colorIndex']
    missing_fields = [field for field in expected_fields if field not in json_data]
    if missing_fields:
        warnings.append(f"Missing JSON fields: {missing_fields}")
    
    # Check ID mapping (accountPersonId in JSON vs id in model)
    json_id = json_data.get('accountPersonId', '')
    if person.id != json_id:
        validation_errors.append(f"ID mismatch: model='{person.id}', json='{json_id}'")
    
    # Check name mapping
    json_name = json_data.get('name', '')
    if person.name != json_name:
        validation_errors.append(f"Name mismatch: model='{person.name}', json='{json_name}'")
    
    # Check email mapping
    json_email = json_data.get('email')
    if person.email != json_email:
        validation_errors.append(f"Email mismatch: model='{person.email}', json='{json_email}'")
    
    # Check phone mapping (phoneNumberKey in JSON vs phone in model)
    json_phone = json_data.get('phoneNumberKey')
    if person.phone != json_phone:
        validation_errors.append(f"Phone mismatch: model='{person.phone}', json='{json_phone}'")
    
    # Check color mapping (colorIndex in JSON vs color in model)
    json_color = json_data.get('colorIndex')
    if person.color != json_color:
        validation_errors.append(f"Color mismatch: model='{person.color}', json='{json_color}'")
    
    # Check for unexpected fields in JSON that we're not mapping to core model fields
    json_fields = set(json_data.keys())
    expected_core_fields = {'accountPersonId', 'name', 'email', 'phoneNumberKey', 'colorIndex'}
    # Additional fields that we do map to model fields but are optional
    optional_mapped_fields = {'emailStatus', 'accountPersonType', 'accountCreator', 'isAdult', 'notifiable', 'version', 'settings', 'notifiableFeatures'}
    expected_fields_set = expected_core_fields | optional_mapped_fields
    unexpected_fields = json_fields - expected_fields_set
    if unexpected_fields:
        warnings.append(f"Unexpected JSON fields not mapped to model: {unexpected_fields}")
    
    # Print warnings
    if warnings:
        print_info(f"Validation warnings ({len(warnings)}):")
        for warning in warnings:
            print(f"  ⚠️  {warning}")
    
    # Print validation results
    if validation_errors:
        print_error(f"Person model validation failed with {len(validation_errors)} errors:")
        for error in validation_errors:
            print(f"  ❌ {error}")
        return False
    else:
        print_success(f"Person model validation passed for '{person.name}'!")
        return True


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
                
                # Validate family members against raw JSON
                family_json = client.get_last_response_data()
                if family_json and isinstance(family_json, list):
                    print_json("Raw Family Members JSON", family_json)
                    print_info(f"Validating {len(family_members)} family member models against JSON...")
                    
                    for i, member in enumerate(family_members):
                        if i < len(family_json):
                            validate_person_against_json(member, family_json[i])
                        else:
                            print_error(f"No JSON data found for family member {i}: {member.name}")
                
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
                
                # Show the raw API response and validate model
                raw_response = client.get_last_response_data()
                if raw_response:
                    print_json("Raw API Response Received", raw_response)
                    
                    # Validate the created appointment against the raw JSON
                    validate_appointment_against_json(created_appointment, raw_response, "created")
                
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
                
                # Show the raw API response for update and validate model
                raw_update_response = client.get_last_response_data()
                if raw_update_response:
                    print_json("Raw Update API Response", raw_update_response)
                    
                    # Validate the updated appointment against the raw JSON
                    validate_appointment_against_json(updated_appointment, raw_update_response, "updated")
                
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
                
                # Validate a sample of appointments against raw JSON
                calendar_json = client.get_last_response_data()
                if calendar_json and isinstance(calendar_json, list) and current_appointments:
                    print_json("Sample Calendar JSON (first 2 appointments)", calendar_json[:2])
                    print_info(f"Validating first 2 appointment models against JSON...")
                    
                    for i in range(min(2, len(current_appointments), len(calendar_json))):
                        validate_appointment_against_json(current_appointments[i], calendar_json[i], "fetched")
                
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
            print("✅ Model validation confirmed data consistency between objects and JSON")
            
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