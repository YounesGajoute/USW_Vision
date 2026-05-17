#!/usr/bin/env python3
"""
Setup initial admin user for Vision Inspection System.
Run this script once during initial setup.
"""

import sys
import os
import getpass

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from config.config import get_config
from src.database.db_manager import DatabaseManager, set_db
from src.api.auth import init_auth_service


def create_admin_user():
    """Create initial admin user."""
    
    print("=== Vision Inspection System - Admin Setup ===")
    print()
    
    # Get configuration
    config = get_config('production')
    
    # Initialize database
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'database',
        'vision.db'
    )
    
    print(f"Database: {db_path}")
    
    db_manager = DatabaseManager(db_path)
    set_db(db_manager)
    
    # Initialize auth service
    auth_service = init_auth_service(db_manager, config)
    
    # Check if admin already exists
    existing_admin = db_manager.get_user_by_username('admin')
    if existing_admin:
        print()
        print("Warning: Admin user already exists!")
        response = input("Do you want to create another admin user? (yes/no): ")
        if response.lower() != 'yes':
            print("Setup cancelled.")
            return
    
    # Get username
    print()
    print("Enter admin user details:")
    print()
    
    while True:
        username = input("Username (3-50 characters): ").strip()
        if len(username) >= 3 and len(username) <= 50:
            # Check if username already exists
            existing_user = db_manager.get_user_by_username(username)
            if existing_user:
                print(f"Error: Username '{username}' already exists. Choose another.")
                continue
            break
        else:
            print("Error: Username must be 3-50 characters.")
    
    # Get password
    while True:
        password = getpass.getpass("Password (min 8 characters, letters + numbers): ")
        password_confirm = getpass.getpass("Confirm password: ")
        
        if password != password_confirm:
            print("Error: Passwords do not match. Try again.")
            continue
        
        if len(password) < 8:
            print("Error: Password must be at least 8 characters.")
            continue
        
        # Check for letters and numbers
        has_letter = any(c.isalpha() for c in password)
        has_number = any(c.isdigit() for c in password)
        
        if not (has_letter and has_number):
            print("Error: Password must contain at least one letter and one number.")
            continue
        
        break
    
    # Create admin user
    try:
        user = auth_service.register_user(
            username=username,
            password=password,
            role='ADMIN'
        )
        
        print()
        print("✓ Admin user created successfully!")
        print(f"  Username: {user['username']}")
        print(f"  Role: {user['role']}")
        print()
        print("You can now log in with these credentials.")
        print()
        
    except Exception as e:
        print()
        print(f"Error creating admin user: {e}")
        sys.exit(1)


if __name__ == '__main__':
    try:
        create_admin_user()
    except KeyboardInterrupt:
        print()
        print("Setup cancelled.")
        sys.exit(0)
    except Exception as e:
        print()
        print(f"Fatal error: {e}")
        sys.exit(1)
