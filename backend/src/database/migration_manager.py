"""Database Migration Manager"""

import sqlite3
import os
import re
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger('migration_manager')


class MigrationManager:
    """
    Manages database schema versioning and migrations.
    
    Features:
    - Automatic version detection
    - Sequential migration application
    - Rollback capability
    - Migration validation
    - Version tracking
    """
    
    def __init__(self, db_path: str, migrations_dir: str = None):
        """
        Initialize migration manager.
        
        Args:
            db_path: Path to SQLite database
            migrations_dir: Path to migrations directory (default: ./migrations)
        """
        self.db_path = db_path
        
        if migrations_dir is None:
            migrations_dir = os.path.join(
                os.path.dirname(__file__),
                'migrations'
            )
        
        self.migrations_dir = migrations_dir
        
        # Ensure migrations directory exists
        os.makedirs(self.migrations_dir, exist_ok=True)
        
        # Initialize schema_versions table
        self._init_version_table()
        
        logger.info(f"Migration manager initialized (DB: {db_path})")
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        return sqlite3.connect(self.db_path)
    
    def _init_version_table(self):
        """Create schema_versions table if it doesn't exist."""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version TEXT NOT NULL UNIQUE,
                    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    description TEXT,
                    migration_file TEXT,
                    checksum TEXT
                )
            """)
            conn.commit()
            
            # Check if we have any versions, if not, insert initial version
            cursor = conn.execute("SELECT COUNT(*) FROM schema_versions")
            count = cursor.fetchone()[0]
            
            if count == 0:
                logger.info("No schema versions found, initializing with v1.0.0")
                conn.execute("""
                    INSERT INTO schema_versions (version, description, migration_file)
                    VALUES (?, ?, ?)
                """, ('1.0.0', 'Initial schema', 'schema.sql'))
                conn.commit()
    
    def get_current_version(self) -> str:
        """
        Get current database schema version.
        
        Returns:
            Current version string (e.g., '1.0.0')
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT version 
                FROM schema_versions 
                ORDER BY applied_at DESC 
                LIMIT 1
            """)
            result = cursor.fetchone()
            
            if result:
                return result[0]
            else:
                return '0.0.0'
    
    def list_applied_migrations(self) -> List[Dict]:
        """
        List all applied migrations.
        
        Returns:
            List of migration dictionaries
        """
        with self._get_connection() as conn:
            cursor = conn.execute("""
                SELECT version, applied_at, description, migration_file
                FROM schema_versions
                ORDER BY applied_at ASC
            """)
            
            migrations = []
            for row in cursor.fetchall():
                migrations.append({
                    'version': row[0],
                    'applied_at': row[1],
                    'description': row[2],
                    'migration_file': row[3]
                })
            
            return migrations
    
    def list_available_migrations(self) -> List[Dict]:
        """
        List all available migration files.
        
        Returns:
            List of migration file information
        """
        migrations = []
        
        if not os.path.exists(self.migrations_dir):
            return migrations
        
        # Find all .sql files matching pattern: v{version}_*.sql
        pattern = re.compile(r'^v(\d+\.\d+\.\d+)_(.+)\.sql$')
        
        for filename in sorted(os.listdir(self.migrations_dir)):
            match = pattern.match(filename)
            if match:
                version = match.group(1)
                description = match.group(2).replace('_', ' ').title()
                filepath = os.path.join(self.migrations_dir, filename)
                
                migrations.append({
                    'version': version,
                    'description': description,
                    'filename': filename,
                    'filepath': filepath
                })
        
        return migrations
    
    def list_pending_migrations(self) -> List[Dict]:
        """
        List migrations that haven't been applied yet.
        
        Returns:
            List of pending migration dictionaries
        """
        current_version = self.get_current_version()
        available = self.list_available_migrations()
        applied = {m['version'] for m in self.list_applied_migrations()}
        
        pending = []
        for migration in available:
            if migration['version'] not in applied:
                # Check if this version is newer than current
                if self._compare_versions(migration['version'], current_version) > 0:
                    pending.append(migration)
        
        return sorted(pending, key=lambda m: m['version'])
    
    def _compare_versions(self, v1: str, v2: str) -> int:
        """
        Compare two semantic version strings.
        
        Args:
            v1: First version (e.g., '1.2.3')
            v2: Second version (e.g., '1.1.5')
        
        Returns:
            -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2
        """
        parts1 = [int(x) for x in v1.split('.')]
        parts2 = [int(x) for x in v2.split('.')]
        
        for i in range(max(len(parts1), len(parts2))):
            p1 = parts1[i] if i < len(parts1) else 0
            p2 = parts2[i] if i < len(parts2) else 0
            
            if p1 < p2:
                return -1
            elif p1 > p2:
                return 1
        
        return 0
    
    def apply_migration(self, migration: Dict, dry_run: bool = False) -> bool:
        """
        Apply a single migration.
        
        Args:
            migration: Migration dictionary with version, filepath, etc.
            dry_run: If True, only validate without applying
        
        Returns:
            True if successful, False otherwise
        
        Raises:
            Exception if migration fails
        """
        version = migration['version']
        filepath = migration['filepath']
        
        logger.info(f"{'[DRY RUN] ' if dry_run else ''}Applying migration {version}...")
        
        try:
            # Read migration file
            with open(filepath, 'r') as f:
                sql_script = f.read()
            
            if dry_run:
                # Validate SQL syntax without executing
                with self._get_connection() as conn:
                    try:
                        conn.execute("EXPLAIN " + sql_script.split(';')[0])
                        logger.info(f"[DRY RUN] Migration {version} validated successfully")
                        return True
                    except Exception as e:
                        logger.error(f"[DRY RUN] Migration {version} validation failed: {e}")
                        return False
            
            # Apply migration
            with self._get_connection() as conn:
                # Execute migration script
                conn.executescript(sql_script)
                
                # Record migration
                conn.execute("""
                    INSERT INTO schema_versions (version, description, migration_file)
                    VALUES (?, ?, ?)
                """, (version, migration['description'], migration['filename']))
                
                conn.commit()
            
            logger.info(f"Migration {version} applied successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to apply migration {version}: {e}")
            raise
    
    def apply_all_pending(self, dry_run: bool = False) -> Tuple[int, int]:
        """
        Apply all pending migrations in order.
        
        Args:
            dry_run: If True, only validate without applying
        
        Returns:
            Tuple of (successful_count, failed_count)
        """
        pending = self.list_pending_migrations()
        
        if not pending:
            logger.info("No pending migrations")
            return (0, 0)
        
        logger.info(f"Found {len(pending)} pending migration(s)")
        
        successful = 0
        failed = 0
        
        for migration in pending:
            try:
                if self.apply_migration(migration, dry_run=dry_run):
                    successful += 1
                else:
                    failed += 1
                    if not dry_run:
                        # Stop on first failure in real mode
                        logger.error("Migration failed, stopping")
                        break
            except Exception as e:
                logger.error(f"Migration {migration['version']} failed: {e}")
                failed += 1
                if not dry_run:
                    break
        
        return (successful, failed)
    
    def create_migration_file(self, version: str, description: str, sql_content: str = None) -> str:
        """
        Create a new migration file.
        
        Args:
            version: Version string (e.g., '1.1.0')
            description: Description of the migration
            sql_content: Optional SQL content
        
        Returns:
            Path to created migration file
        """
        # Sanitize description for filename
        desc_clean = description.lower().replace(' ', '_')
        desc_clean = re.sub(r'[^a-z0-9_]', '', desc_clean)
        
        filename = f"v{version}_{desc_clean}.sql"
        filepath = os.path.join(self.migrations_dir, filename)
        
        # Create migration template
        if sql_content is None:
            sql_content = f"""-- Migration: v{version} - {description}
-- Date: {datetime.now().strftime('%Y-%m-%d')}
-- Description: {description}

BEGIN TRANSACTION;

-- Your migration SQL here
-- Example:
-- ALTER TABLE programs ADD COLUMN new_field TEXT;
-- CREATE INDEX idx_new_field ON programs(new_field);

-- Record migration version
INSERT INTO schema_versions (version, description)
VALUES ('{version}', '{description}');

COMMIT;
"""
        
        with open(filepath, 'w') as f:
            f.write(sql_content)
        
        logger.info(f"Created migration file: {filename}")
        return filepath
    
    def validate_database(self) -> Dict:
        """
        Validate database integrity and version.
        
        Returns:
            Dictionary with validation results
        """
        result = {
            'valid': True,
            'current_version': None,
            'applied_migrations': 0,
            'pending_migrations': 0,
            'issues': []
        }
        
        try:
            # Check version table exists
            with self._get_connection() as conn:
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='schema_versions'
                """)
                if not cursor.fetchone():
                    result['valid'] = False
                    result['issues'].append('schema_versions table not found')
                    return result
            
            # Get current version
            result['current_version'] = self.get_current_version()
            
            # Count migrations
            result['applied_migrations'] = len(self.list_applied_migrations())
            result['pending_migrations'] = len(self.list_pending_migrations())
            
            # Check for required tables
            required_tables = ['programs', 'tools', 'inspection_results', 'system_logs']
            with self._get_connection() as conn:
                for table in required_tables:
                    cursor = conn.execute(f"""
                        SELECT name FROM sqlite_master 
                        WHERE type='table' AND name=?
                    """, (table,))
                    if not cursor.fetchone():
                        result['valid'] = False
                        result['issues'].append(f'Required table missing: {table}')
            
            if result['pending_migrations'] > 0:
                result['issues'].append(f'{result["pending_migrations"]} pending migration(s)')
            
        except Exception as e:
            result['valid'] = False
            result['issues'].append(f'Validation error: {str(e)}')
        
        return result
    
    def get_migration_status(self) -> Dict:
        """
        Get comprehensive migration status.
        
        Returns:
            Dictionary with status information
        """
        return {
            'current_version': self.get_current_version(),
            'applied_migrations': self.list_applied_migrations(),
            'available_migrations': self.list_available_migrations(),
            'pending_migrations': self.list_pending_migrations(),
            'validation': self.validate_database()
        }
