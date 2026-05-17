"""Database Manager for Vision Inspection System"""

import sqlite3
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from contextlib import contextmanager
import threading


class DatabaseManager:
    """
    Manages all database operations with connection pooling and thread safety.
    Provides CRUD operations for programs, tools, and inspection results.
    """
    
    def __init__(self, db_path: str):
        """
        Initialize database manager.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()
        
        # Ensure database directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        # Initialize database schema
        self._init_schema()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, 'connection'):
            self._local.connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=10.0
            )
            self._local.connection.row_factory = sqlite3.Row
            # Enable foreign keys
            self._local.connection.execute("PRAGMA foreign_keys = ON")
            try:
                self._local.connection.execute("PRAGMA journal_mode = WAL")
            except sqlite3.Error:
                pass
        return self._local.connection
    
    @contextmanager
    def _get_cursor(self):
        """Context manager for database cursor with automatic commit/rollback."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            try:
                yield cursor
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                cursor.close()
    
    def _init_schema(self):
        """Initialize database schema from SQL file."""
        schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
        
        if not os.path.exists(schema_path):
            raise FileNotFoundError(f"Schema file not found: {schema_path}")
        
        with open(schema_path, 'r') as f:
            schema_sql = f.read()
        
        with self._get_cursor() as cursor:
            cursor.executescript(schema_sql)
    
    # ==================== PROGRAM OPERATIONS ====================
    
    def create_program(self, name: str, config: Dict) -> int:
        """
        Create a new inspection program.
        
        Args:
            name: Program name (must be unique)
            config: Program configuration dictionary
            
        Returns:
            Program ID
            
        Raises:
            ValueError: If program name already exists
        """
        config_json = json.dumps(config)
        
        try:
            with self._get_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO programs (name, config_json, master_image_path)
                    VALUES (?, ?, ?)
                """, (name, config_json, config.get('masterImage')))
                
                program_id = cursor.lastrowid
                
                # Insert tools if present
                if 'tools' in config and config['tools']:
                    self._insert_tools(cursor, program_id, config['tools'])
                
                return program_id
                
        except sqlite3.IntegrityError:
            raise ValueError(f"Program with name '{name}' already exists")
    
    def _insert_tools(self, cursor: sqlite3.Cursor, program_id: int, tools: List[Dict]):
        """Insert tools for a program."""
        for order, tool in enumerate(tools):
            roi = tool['roi']
            cursor.execute("""
                INSERT INTO tools (
                    program_id, tool_type, name, color,
                    roi_x, roi_y, roi_width, roi_height,
                    threshold, upper_limit, parameters_json, tool_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                program_id,
                tool['type'],
                tool['name'],
                tool['color'],
                roi['x'],
                roi['y'],
                roi['width'],
                roi['height'],
                tool['threshold'],
                tool.get('upperLimit'),
                json.dumps(tool.get('parameters', {})),
                order
            ))
    
    def get_program(self, program_id: int) -> Optional[Dict]:
        """
        Get program by ID.
        
        Args:
            program_id: Program ID
            
        Returns:
            Program dictionary or None if not found
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM programs WHERE id = ?
            """, (program_id,))
            
            row = cursor.fetchone()
            if not row:
                return None
            
            program = dict(row)
            program['config'] = json.loads(program['config_json'])
            del program['config_json']
            
            # Calculate stats on the fly
            program['success_rate'] = (
                (program['ok_count'] / program['total_inspections'] * 100)
                if program['total_inspections'] > 0 else 0
            )
            program['tool_count'] = len(program['config'].get('tools', []))
            
            return program
    
    def get_program_by_name(self, name: str) -> Optional[Dict]:
        """Get program by name."""
        with self._get_cursor() as cursor:
            cursor.execute("SELECT * FROM programs WHERE name = ?", (name,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            program = dict(row)
            program['config'] = json.loads(program['config_json'])
            del program['config_json']
            
            return program
    
    def list_programs(self, active_only: bool = True) -> List[Dict]:
        """
        List all programs.
        
        Args:
            active_only: If True, only return active programs
            
        Returns:
            List of program dictionaries
        """
        with self._get_cursor() as cursor:
            query = "SELECT * FROM program_stats"
            if active_only:
                query += " WHERE is_active = 1"
            query += " ORDER BY updated_at DESC"
            
            cursor.execute(query)
            rows = cursor.fetchall()
            
            programs = []
            for row in rows:
                program = dict(row)
                # Fetch full config for each program
                cursor.execute(
                    "SELECT config_json FROM programs WHERE id = ?",
                    (program['id'],)
                )
                config_row = cursor.fetchone()
                if config_row:
                    program['config'] = json.loads(config_row['config_json'])
                programs.append(program)
            
            return programs
    
    def update_program(self, program_id: int, updates: Dict) -> bool:
        """
        Update program configuration.
        
        Args:
            program_id: Program ID
            updates: Dictionary of fields to update
            
        Returns:
            True if successful, False if program not found
        """
        allowed_fields = ['name', 'config', 'master_image_path', 'is_active', 'description']
        
        update_fields = []
        values = []
        
        for field, value in updates.items():
            if field == 'config':
                update_fields.append('config_json = ?')
                values.append(json.dumps(value))
            elif field in allowed_fields:
                update_fields.append(f'{field} = ?')
                values.append(value)
        
        if not update_fields:
            return False
        
        values.append(program_id)
        
        with self._get_cursor() as cursor:
            # Update program
            cursor.execute(f"""
                UPDATE programs
                SET {', '.join(update_fields)}
                WHERE id = ?
            """, tuple(values))
            
            # If config was updated, update tools
            if 'config' in updates and 'tools' in updates['config']:
                # Delete existing tools
                cursor.execute("DELETE FROM tools WHERE program_id = ?", (program_id,))
                # Insert new tools
                self._insert_tools(cursor, program_id, updates['config']['tools'])
            
            return cursor.rowcount > 0
    
    def delete_program(self, program_id: int) -> bool:
        """
        Delete a program (soft delete by setting is_active=0).
        
        Args:
            program_id: Program ID
            
        Returns:
            True if successful
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE programs SET is_active = 0 WHERE id = ?
            """, (program_id,))
            return cursor.rowcount > 0
    
    def hard_delete_program(self, program_id: int) -> bool:
        """Permanently delete a program and all associated data (CASCADE)."""
        with self._get_cursor() as cursor:
            cursor.execute("DELETE FROM programs WHERE id = ?", (program_id,))
            return cursor.rowcount > 0

    def list_inspection_image_paths(self, program_id: int) -> List[str]:
        """Return stored image paths for a program's inspection results."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT image_path FROM inspection_results
                WHERE program_id = ? AND image_path IS NOT NULL AND image_path != ''
                """,
                (program_id,),
            )
            return [row[0] for row in cursor.fetchall()]
    
    # ==================== INSPECTION RESULTS ====================
    
    def log_inspection_result(
        self,
        program_id: int,
        status: str,
        processing_time_ms: float,
        tool_results: List[Dict],
        trigger_type: str,
        image_path: Optional[str] = None,
        notes: Optional[str] = None
    ) -> int:
        """
        Log an inspection result.
        
        Args:
            program_id: Program ID
            status: Overall status (OK or NG)
            processing_time_ms: Processing time in milliseconds
            tool_results: List of tool result dictionaries
            trigger_type: Trigger type (internal or external)
            image_path: Optional path to captured image
            notes: Optional notes
            
        Returns:
            Result ID
        """
        tool_results_json = json.dumps(tool_results)
        
        with self._get_cursor() as cursor:
            # Insert result
            cursor.execute("""
                INSERT INTO inspection_results (
                    program_id, overall_status, processing_time_ms,
                    tool_results_json, image_path, trigger_type, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                program_id, status, processing_time_ms,
                tool_results_json, image_path, trigger_type, notes
            ))
            
            result_id = cursor.lastrowid
            
            # Update program statistics
            cursor.execute("""
                UPDATE programs
                SET 
                    total_inspections = total_inspections + 1,
                    ok_count = ok_count + ?,
                    ng_count = ng_count + ?,
                    last_run = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                1 if status == 'OK' else 0,
                1 if status == 'NG' else 0,
                program_id
            ))
            
            return result_id
    
    def get_inspection_history(
        self,
        program_id: Optional[int] = None,
        limit: int = 100,
        status_filter: Optional[str] = None
    ) -> List[Dict]:
        """
        Get inspection history.
        
        Args:
            program_id: Optional program ID to filter by
            limit: Maximum number of results
            status_filter: Optional status filter (OK or NG)
            
        Returns:
            List of inspection result dictionaries
        """
        with self._get_cursor() as cursor:
            query = "SELECT * FROM inspection_results WHERE 1=1"
            params = []
            
            if program_id:
                query += " AND program_id = ?"
                params.append(program_id)
            
            if status_filter:
                query += " AND overall_status = ?"
                params.append(status_filter)
            
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            
            results = []
            for row in rows:
                result = dict(row)
                result['tool_results'] = json.loads(result['tool_results_json'])
                del result['tool_results_json']
                results.append(result)
            
            return results
    
    def get_inspection_result_by_id(
        self, program_id: int, result_id: int
    ) -> Optional[Dict]:
        """
        Return a single inspection result row (parsed tool_results), or None.
        """
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM inspection_results
                WHERE program_id = ? AND id = ?
                """,
                (program_id, result_id),
            )
            row = cursor.fetchone()
            if not row:
                return None
            result = dict(row)
            result['tool_results'] = json.loads(result['tool_results_json'])
            del result['tool_results_json']
            return result
    
    # ==================== SYSTEM LOGS ====================
    
    def log_event(
        self,
        level: str,
        category: str,
        message: str,
        details: Optional[Dict] = None,
        program_id: Optional[int] = None
    ):
        """
        Log a system event.
        
        Args:
            level: Log level (INFO, WARNING, ERROR, CRITICAL)
            category: Event category
            message: Log message
            details: Optional additional details as dictionary
            program_id: Optional associated program ID
        """
        details_json = json.dumps(details) if details else None
        
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO system_logs (level, category, message, details_json, program_id)
                VALUES (?, ?, ?, ?, ?)
            """, (level, category, message, details_json, program_id))
    
    def get_logs(
        self,
        level: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """Get system logs with optional filtering."""
        with self._get_cursor() as cursor:
            query = "SELECT * FROM system_logs WHERE 1=1"
            params = []
            
            if level:
                query += " AND level = ?"
                params.append(level)
            
            if category:
                query += " AND category = ?"
                params.append(category)
            
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            
            logs = []
            for row in rows:
                log = dict(row)
                if log['details_json']:
                    log['details'] = json.loads(log['details_json'])
                del log['details_json']
                logs.append(log)
            
            return logs
    
    # ==================== USER MANAGEMENT ====================
    
    def create_user(self, username: str, password_hash: str, role: str) -> int:
        """
        Create a new user.
        
        Args:
            username: Username
            password_hash: Hashed password
            role: User role
        
        Returns:
            User ID
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO users (username, password_hash, role)
                VALUES (?, ?, ?)
            """, (username, password_hash, role))
            return cursor.lastrowid
    
    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        """Get user by ID."""
        with self._get_cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Get user by username."""
        with self._get_cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def update_user_password(self, user_id: int, password_hash: str):
        """Update user password."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users SET password_hash = ? WHERE id = ?
            """, (password_hash, user_id))
    
    def update_last_login(self, user_id: int):
        """Update last login timestamp."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?
            """, (user_id,))
    
    def increment_failed_login_attempts(self, user_id: int):
        """Increment failed login attempts counter."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users 
                SET failed_login_attempts = failed_login_attempts + 1,
                    last_failed_login = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (user_id,))
    
    def reset_failed_login_attempts(self, user_id: int):
        """Reset failed login attempts counter."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users SET failed_login_attempts = 0 WHERE id = ?
            """, (user_id,))
    
    def get_failed_login_attempts(self, user_id: int) -> int:
        """Get number of failed login attempts."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT failed_login_attempts FROM users WHERE id = ?
            """, (user_id,))
            row = cursor.fetchone()
            return row['failed_login_attempts'] if row else 0
    
    def lock_user_account(self, user_id: int):
        """Lock user account."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users SET is_locked = 1 WHERE id = ?
            """, (user_id,))
    
    def unlock_user_account(self, user_id: int):
        """Unlock user account."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users 
                SET is_locked = 0, failed_login_attempts = 0 
                WHERE id = ?
            """, (user_id,))
    
    def list_users(self) -> List[Dict]:
        """List all users."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, username, role, is_active, is_locked, 
                       created_at, last_login
                FROM users
                ORDER BY created_at DESC
            """)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    
    def update_user(self, user_id: int, updates: Dict) -> bool:
        """Update user properties."""
        allowed_fields = ['role', 'is_active', 'is_locked']
        
        update_fields = []
        values = []
        
        for field, value in updates.items():
            if field in allowed_fields:
                update_fields.append(f'{field} = ?')
                values.append(value)
        
        if not update_fields:
            return False
        
        values.append(user_id)
        
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                UPDATE users SET {', '.join(update_fields)} WHERE id = ?
            """, tuple(values))
            return cursor.rowcount > 0
    
    def delete_user(self, user_id: int) -> bool:
        """Soft delete user (set is_active = 0)."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users SET is_active = 0 WHERE id = ?
            """, (user_id,))
            return cursor.rowcount > 0
    
    # ==================== TOKEN MANAGEMENT ====================
    
    def store_refresh_token(self, user_id: int, token_hash: str, expires_at: datetime):
        """Store refresh token."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO refresh_tokens (user_id, token_hash, expires_at)
                VALUES (?, ?, ?)
            """, (user_id, token_hash, expires_at))
    
    def is_token_revoked(self, token_hash: str) -> bool:
        """Check if token is revoked."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT revoked FROM refresh_tokens WHERE token_hash = ?
            """, (token_hash,))
            row = cursor.fetchone()
            return row['revoked'] if row else True
    
    def revoke_token(self, token_hash: str):
        """Revoke a refresh token."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?
            """, (token_hash,))
    
    def revoke_all_user_tokens(self, user_id: int):
        """Revoke all tokens for a user."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?
            """, (user_id,))
    
    def cleanup_expired_tokens(self):
        """Delete expired tokens."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM refresh_tokens WHERE expires_at < CURRENT_TIMESTAMP
            """)
    
    # ==================== AUDIT LOGGING ====================
    
    def log_audit_event(
        self,
        user_id: int,
        action: str,
        resource_type: str = None,
        resource_id: int = None,
        details: Dict = None,
        request_id: str = None,
        ip_address: str = None
    ):
        """Log user action for audit trail."""
        details_json = json.dumps(details) if details else None
        
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO audit_log (
                    user_id, action, resource_type, resource_id,
                    details_json, request_id, ip_address
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, action, resource_type, resource_id, details_json, request_id, ip_address))
    
    def log_failed_login_attempt(self, username: str, reason: str, ip_address: str = None):
        """Log failed login attempt."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO failed_login_attempts (username, reason, ip_address)
                VALUES (?, ?, ?)
            """, (username, reason, ip_address))
    
    def get_audit_log(
        self,
        user_id: int = None,
        action: str = None,
        resource_type: str = None,
        limit: int = 100
    ) -> List[Dict]:
        """Get audit log entries."""
        with self._get_cursor() as cursor:
            query = "SELECT * FROM audit_log WHERE 1=1"
            params = []
            
            if user_id:
                query += " AND user_id = ?"
                params.append(user_id)
            
            if action:
                query += " AND action = ?"
                params.append(action)
            
            if resource_type:
                query += " AND resource_type = ?"
                params.append(resource_type)
            
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            
            logs = []
            for row in rows:
                log = dict(row)
                if log.get('details_json'):
                    log['details'] = json.loads(log['details_json'])
                    del log['details_json']
                logs.append(log)
            
            return logs
    
    # ==================== CONVENIENCE METHODS ====================
    
    def get_inspection_results(self, program_id: int = None, limit: int = 100, status_filter: str = None) -> List[Dict]:
        """
        Convenience method for get_inspection_history.
        Get inspection results with optional filtering.
        
        Args:
            program_id: Optional program ID to filter by
            limit: Maximum number of results
            status_filter: Optional status filter (OK or NG)
            
        Returns:
            List of inspection result dictionaries
        """
        return self.get_inspection_history(program_id=program_id, limit=limit, status_filter=status_filter)
    
    def get_system_logs(self, level: str = None, category: str = None, limit: int = 100) -> List[Dict]:
        """
        Convenience method for get_logs.
        Get system logs with optional filtering.
        
        Args:
            level: Optional log level filter
            category: Optional category filter
            limit: Maximum number of results
            
        Returns:
            List of log dictionaries
        """
        return self.get_logs(level=level, category=category, limit=limit)
    
    def close(self):
        """Close database connection."""
        if hasattr(self._local, 'connection'):
            self._local.connection.close()
            delattr(self._local, 'connection')


def init_database(config: Dict):
    """
    Initialize database from configuration.
    
    Args:
        config: Database configuration dictionary
        
    Returns:
        DatabaseManager instance
    """
    db_path = config.get('path', './database/vision.db')
    return DatabaseManager(db_path)


# Global database instance
_db_instance: Optional[DatabaseManager] = None


def get_db() -> DatabaseManager:
    """Get global database instance."""
    global _db_instance
    if _db_instance is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _db_instance


def set_db(db: DatabaseManager):
    """Set global database instance."""
    global _db_instance
    _db_instance = db

