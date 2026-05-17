/**
 * Data Migration Utility
 * 
 * Handles one-time migration of localStorage data to backend API.
 * 
 * Features:
 * - Automatic detection of localStorage data
 * - Safe migration with verification
 * - Rollback capability
 * - Detailed logging
 * - Dry-run mode
 */

import { Program, storage } from './storage';
import { apiService } from './api-service';

// ==================== TYPES ====================

export interface MigrationStatus {
  needed: boolean;
  programCount: number;
  estimatedSize: number;
  lastMigrationAttempt?: string;
  migrationCompleted?: string;
}

export interface MigrationResult {
  success: boolean;
  programsMigrated: number;
  imagesMigrated: number;
  errors: Array<{
    program?: string;
    error: string;
  }>;
  backupCreated: boolean;
  duration: number;
}

export interface MigrationOptions {
  dryRun?: boolean;
  skipBackup?: boolean;
  continueOnError?: boolean;
}

// ==================== CONSTANTS ====================

const MIGRATION_STATUS_KEY = 'migration_status';
const MIGRATION_BACKUP_KEY = 'vision_programs_backup';
const MIGRATION_COMPLETED_KEY = 'migration_completed';

// ==================== MIGRATION MANAGER ====================

export class MigrationManager {
  /**
   * Check if migration is needed
   */
  static checkMigrationStatus(): MigrationStatus {
    // Check if migration already completed
    const migrationCompleted = localStorage.getItem(MIGRATION_COMPLETED_KEY);
    
    // Check for localStorage data
    const localPrograms = storage.getAllPrograms();
    
    // Calculate estimated size
    const dataString = localStorage.getItem('vision_programs') || '[]';
    const estimatedSize = new Blob([dataString]).size;
    
    return {
      needed: localPrograms.length > 0 && !migrationCompleted,
      programCount: localPrograms.length,
      estimatedSize,
      migrationCompleted: migrationCompleted || undefined,
      lastMigrationAttempt: localStorage.getItem('last_migration_attempt') || undefined
    };
  }
  
  /**
   * Perform data migration from localStorage to backend
   */
  static async migrate(options: MigrationOptions = {}): Promise<MigrationResult> {
    const startTime = Date.now();
    
    const result: MigrationResult = {
      success: false,
      programsMigrated: 0,
      imagesMigrated: 0,
      errors: [],
      backupCreated: false,
      duration: 0
    };
    
    const isDryRun = options.dryRun || false;
    
    console.log(`${isDryRun ? '[DRY RUN] ' : ''}Starting migration...`);
    
    try {
      // 1. Check if backend is available
      const backendAvailable = await apiService.checkHealth();
      
      if (!backendAvailable) {
        throw new Error('Backend API is not available. Please ensure the backend server is running.');
      }
      
      // 2. Load localStorage data
      const localPrograms = storage.getAllPrograms();
      
      if (localPrograms.length === 0) {
        console.log('No programs to migrate');
        result.success = true;
        result.duration = Date.now() - startTime;
        return result;
      }
      
      console.log(`Found ${localPrograms.length} program(s) to migrate`);
      
      // 3. Create backup (unless skipped)
      if (!options.skipBackup && !isDryRun) {
        try {
          const backup = JSON.stringify(localPrograms);
          localStorage.setItem(MIGRATION_BACKUP_KEY, backup);
          result.backupCreated = true;
          console.log('Created backup of localStorage data');
        } catch (error: any) {
          console.warn('Failed to create backup:', error.message);
        }
      }
      
      // 4. Record migration attempt
      if (!isDryRun) {
        localStorage.setItem('last_migration_attempt', new Date().toISOString());
      }
      
      // 5. Migrate each program
      for (const program of localPrograms) {
        try {
          console.log(`${isDryRun ? '[DRY RUN] ' : ''}Migrating program: ${program.name}`);
          
          // Extract master image (if present)
          const masterImageBase64 = program.config?.masterImage;
          
          if (isDryRun) {
            // In dry-run mode, just validate and log
            console.log(`  - Would create program: ${program.name}`);
            if (masterImageBase64) {
              console.log(`  - Would upload master image (${this.estimateBase64Size(masterImageBase64)} KB)`);
            }
            result.programsMigrated++;
            if (masterImageBase64) result.imagesMigrated++;
            continue;
          }
          
          // Create program config without master image
          const programConfig = {
            ...program.config,
            masterImage: null // Will upload separately
          };
          
          // Create program via API
          const createdProgram = await apiService.createProgram({
            name: program.name,
            config: programConfig
          });
          
          console.log(`  ✓ Created program with ID: ${createdProgram.id}`);
          result.programsMigrated++;
          
          // Upload master image if present
          if (masterImageBase64) {
            try {
              const blob = this.base64ToBlob(masterImageBase64);
              await apiService.uploadMasterImage(
                parseInt(createdProgram.id),
                blob
              );
              console.log(`  ✓ Uploaded master image`);
              result.imagesMigrated++;
            } catch (error: any) {
              console.warn(`  ⚠ Failed to upload master image: ${error.message}`);
              result.errors.push({
                program: program.name,
                error: `Image upload failed: ${error.message}`
              });
              
              if (!options.continueOnError) {
                throw error;
              }
            }
          }
          
          // Migrate stats if present
          if (program.totalInspections > 0) {
            try {
              await apiService.updateProgramStats(parseInt(createdProgram.id), {
                totalInspections: program.totalInspections,
                okCount: program.okCount,
                ngCount: program.ngCount,
                lastRun: program.lastRun || null
              });
              console.log(`  ✓ Migrated statistics`);
            } catch (error: any) {
              console.warn(`  ⚠ Failed to migrate stats: ${error.message}`);
              // Non-critical, continue
            }
          }
          
        } catch (error: any) {
          console.error(`  ✗ Failed to migrate program ${program.name}:`, error.message);
          result.errors.push({
            program: program.name,
            error: error.message
          });
          
          if (!options.continueOnError) {
            throw error;
          }
        }
      }
      
      // 6. Verify migration
      if (!isDryRun && result.programsMigrated > 0) {
        try {
          const backendPrograms = await apiService.getPrograms();
          console.log(`Verification: Backend has ${backendPrograms.length} program(s)`);
        } catch (error: any) {
          console.warn('Could not verify migration:', error.message);
        }
      }
      
      // 7. Mark migration as complete
      if (!isDryRun && result.errors.length === 0) {
        localStorage.setItem(MIGRATION_COMPLETED_KEY, new Date().toISOString());
        console.log('✓ Migration completed successfully');
        
        // Clear active localStorage data (keep backup)
        localStorage.removeItem('vision_programs');
        console.log('Cleared localStorage data (backup retained)');
      }
      
      result.success = result.errors.length === 0;
      result.duration = Date.now() - startTime;
      
      console.log(`Migration ${isDryRun ? 'simulation ' : ''}completed in ${result.duration}ms`);
      console.log(`  - Programs: ${result.programsMigrated}/${localPrograms.length}`);
      console.log(`  - Images: ${result.imagesMigrated}`);
      console.log(`  - Errors: ${result.errors.length}`);
      
      return result;
      
    } catch (error: any) {
      result.success = false;
      result.errors.push({
        error: `Migration failed: ${error.message}`
      });
      result.duration = Date.now() - startTime;
      
      console.error('Migration failed:', error);
      
      return result;
    }
  }
  
  /**
   * Rollback migration (restore from backup)
   */
  static async rollback(): Promise<boolean> {
    try {
      const backup = localStorage.getItem(MIGRATION_BACKUP_KEY);
      
      if (!backup) {
        console.warn('No backup found to restore');
        return false;
      }
      
      // Restore backup
      localStorage.setItem('vision_programs', backup);
      
      // Clear migration flags
      localStorage.removeItem(MIGRATION_COMPLETED_KEY);
      localStorage.removeItem('last_migration_attempt');
      
      console.log('✓ Rollback completed - localStorage data restored');
      
      return true;
      
    } catch (error: any) {
      console.error('Rollback failed:', error);
      return false;
    }
  }
  
  /**
   * Clean up migration artifacts
   */
  static cleanup(): void {
    try {
      localStorage.removeItem(MIGRATION_BACKUP_KEY);
      localStorage.removeItem('last_migration_attempt');
      console.log('Migration artifacts cleaned up');
    } catch (error: any) {
      console.error('Cleanup failed:', error);
    }
  }
  
  /**
   * Reset migration status (for testing)
   */
  static resetMigrationStatus(): void {
    localStorage.removeItem(MIGRATION_COMPLETED_KEY);
    localStorage.removeItem('last_migration_attempt');
    console.log('Migration status reset');
  }
  
  // ==================== UTILITY METHODS ====================
  
  private static base64ToBlob(base64: string): Blob {
    // Remove data URL prefix if present
    const base64Data = base64.replace(/^data:image\/\w+;base64,/, '');
    
    // Decode base64
    const byteString = atob(base64Data);
    
    // Create array buffer
    const arrayBuffer = new ArrayBuffer(byteString.length);
    const uint8Array = new Uint8Array(arrayBuffer);
    
    for (let i = 0; i < byteString.length; i++) {
      uint8Array[i] = byteString.charCodeAt(i);
    }
    
    // Determine MIME type
    const mimeType = base64.match(/^data:(image\/\w+);base64,/)?.[1] || 'image/jpeg';
    
    return new Blob([arrayBuffer], { type: mimeType });
  }
  
  private static estimateBase64Size(base64: string): number {
    // Remove data URL prefix
    const base64Data = base64.replace(/^data:image\/\w+;base64,/, '');
    // Calculate size in KB
    return Math.round((base64Data.length * 3) / 4 / 1024);
  }
  
  /**
   * Get detailed migration report
   */
  static getMigrationReport(): {
    status: MigrationStatus;
    hasBackup: boolean;
    backupSize: number;
  } {
    const status = this.checkMigrationStatus();
    const backup = localStorage.getItem(MIGRATION_BACKUP_KEY);
    
    return {
      status,
      hasBackup: !!backup,
      backupSize: backup ? new Blob([backup]).size : 0
    };
  }
}

// ==================== EXPORT ====================

export const migrationManager = MigrationManager;

export default MigrationManager;
