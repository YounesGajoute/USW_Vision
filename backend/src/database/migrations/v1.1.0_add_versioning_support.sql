-- Migration: v1.1.0 - Add versioning support
-- Date: 2025-10-08
-- Description: Adds schema versioning, backup tracking, and data integrity fields

BEGIN TRANSACTION;

-- Add new columns to programs table
ALTER TABLE programs ADD COLUMN schema_version TEXT DEFAULT '1.0.0';
ALTER TABLE programs ADD COLUMN data_checksum TEXT;
ALTER TABLE programs ADD COLUMN last_backup DATETIME;
ALTER TABLE programs ADD COLUMN migration_status TEXT DEFAULT 'current';

-- Update existing records to have proper version
UPDATE programs SET schema_version = '1.0.0' WHERE schema_version IS NULL;

-- Create backups table for tracking system backups
CREATE TABLE IF NOT EXISTS system_backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backup_id TEXT NOT NULL UNIQUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    backup_type TEXT NOT NULL,  -- full, incremental, programs_only
    file_path TEXT,
    file_size INTEGER,
    program_count INTEGER,
    image_count INTEGER,
    result_count INTEGER,
    status TEXT DEFAULT 'completed',  -- completed, failed, in_progress
    metadata_json TEXT,
    created_by TEXT,
    description TEXT
);

-- Create index for faster backup lookups
CREATE INDEX IF NOT EXISTS idx_backups_created ON system_backups(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_backups_type ON system_backups(backup_type);

-- Add trigger to update last_backup timestamp
CREATE TRIGGER IF NOT EXISTS update_program_backup_time
AFTER INSERT ON system_backups
BEGIN
    UPDATE programs SET last_backup = CURRENT_TIMESTAMP;
END;

-- Insert initial data into schema_versions (handled by migration_manager)
-- This migration will be recorded automatically

COMMIT;
