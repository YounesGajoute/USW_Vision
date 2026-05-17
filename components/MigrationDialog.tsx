/**
 * Migration Dialog Component
 * 
 * Automatically detects localStorage data and prompts user to migrate
 * to the new API-based storage system.
 * 
 * Usage:
 * - Add to your main layout or app component
 * - Will automatically show if migration is needed
 * - Handles the entire migration process
 * - Provides feedback and error handling
 */

'use client';

import { useState, useEffect } from 'react';
import { X, Database, CheckCircle, AlertCircle, Info } from 'lucide-react';
import { MigrationManager, type MigrationResult, type MigrationStatus } from '@/lib/migration-utility';

interface MigrationDialogProps {
  /**
   * Callback when migration completes successfully
   */
  onMigrationComplete?: () => void;
  
  /**
   * Callback when migration is skipped
   */
  onSkip?: () => void;
}

export function MigrationDialog({ 
  onMigrationComplete, 
  onSkip 
}: MigrationDialogProps) {
  const [status, setStatus] = useState<MigrationStatus | null>(null);
  const [migrating, setMigrating] = useState(false);
  const [result, setResult] = useState<MigrationResult | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  
  // Check migration status on mount
  useEffect(() => {
    checkStatus();
  }, []);
  
  function checkStatus() {
    const migrationStatus = MigrationManager.checkMigrationStatus();
    setStatus(migrationStatus);
  }
  
  async function handleMigrate() {
    setMigrating(true);
    setResult(null);
    
    try {
      const migrationResult = await MigrationManager.migrate({
        dryRun: false,
        skipBackup: false,
        continueOnError: true
      });
      
      setResult(migrationResult);
      
      if (migrationResult.success) {
        // Wait a moment to show success message
        setTimeout(() => {
          onMigrationComplete?.();
          setDismissed(true);
        }, 2000);
      }
    } catch (error: any) {
      console.error('Migration error:', error);
      setResult({
        success: false,
        programsMigrated: 0,
        imagesMigrated: 0,
        errors: [{ error: error.message }],
        backupCreated: false,
        duration: 0
      });
    } finally {
      setMigrating(false);
    }
  }
  
  async function handleDryRun() {
    setMigrating(true);
    
    try {
      const dryRunResult = await MigrationManager.migrate({
        dryRun: true
      });
      
      setResult(dryRunResult);
      setShowDetails(true);
    } catch (error: any) {
      console.error('Dry run error:', error);
    } finally {
      setMigrating(false);
    }
  }
  
  function handleSkip() {
    setDismissed(true);
    onSkip?.();
  }
  
  function handleRollback() {
    if (confirm('Are you sure you want to rollback the migration? This will restore your localStorage data.')) {
      MigrationManager.rollback();
      window.location.reload();
    }
  }
  
  // Don't show if no migration needed or dismissed
  if (!status || !status.needed || dismissed) {
    return null;
  }
  
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
      <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full mx-4 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-blue-100 rounded-lg">
              <Database className="w-6 h-6 text-blue-600" />
            </div>
            <div>
              <h2 className="text-xl font-semibold text-gray-900">
                Storage System Upgrade
              </h2>
              <p className="text-sm text-gray-500">
                Migrate to improved storage system
              </p>
            </div>
          </div>
          <button
            onClick={handleSkip}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
            aria-label="Dismiss"
          >
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>
        
        {/* Content */}
        <div className="p-6 space-y-4">
          {/* Info Box */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
            <div className="flex gap-3">
              <Info className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <h3 className="font-medium text-blue-900 mb-1">
                  Why migrate?
                </h3>
                <ul className="text-sm text-blue-800 space-y-1">
                  <li>• Reliable database storage (no data loss from cache clearing)</li>
                  <li>• Automatic backup and restore capabilities</li>
                  <li>• Support for more programs and larger images</li>
                  <li>• Better performance and faster loading</li>
                </ul>
              </div>
            </div>
          </div>
          
          {/* Status Info */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-gray-50 rounded-lg p-4">
              <div className="text-sm text-gray-500 mb-1">Programs to migrate</div>
              <div className="text-2xl font-bold text-gray-900">
                {status.programCount}
              </div>
            </div>
            <div className="bg-gray-50 rounded-lg p-4">
              <div className="text-sm text-gray-500 mb-1">Estimated size</div>
              <div className="text-2xl font-bold text-gray-900">
                {(status.estimatedSize / 1024).toFixed(1)} KB
              </div>
            </div>
          </div>
          
          {/* Result Display */}
          {result && (
            <div
              className={`rounded-lg p-4 ${
                result.success
                  ? 'bg-green-50 border border-green-200'
                  : 'bg-red-50 border border-red-200'
              }`}
            >
              <div className="flex gap-3">
                {result.success ? (
                  <CheckCircle className="w-5 h-5 text-green-600 flex-shrink-0" />
                ) : (
                  <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0" />
                )}
                <div className="flex-1">
                  <h4
                    className={`font-medium mb-2 ${
                      result.success ? 'text-green-900' : 'text-red-900'
                    }`}
                  >
                    {result.success
                      ? '✓ Migration Successful!'
                      : '✗ Migration Failed'}
                  </h4>
                  
                  <div
                    className={`text-sm space-y-1 ${
                      result.success ? 'text-green-800' : 'text-red-800'
                    }`}
                  >
                    <p>Programs migrated: {result.programsMigrated}</p>
                    <p>Images migrated: {result.imagesMigrated}</p>
                    <p>Duration: {(result.duration / 1000).toFixed(1)}s</p>
                    
                    {result.errors.length > 0 && (
                      <div className="mt-2">
                        <p className="font-medium">Errors:</p>
                        <ul className="list-disc list-inside space-y-1 mt-1">
                          {result.errors.map((err, i) => (
                            <li key={i}>
                              {err.program ? `${err.program}: ` : ''}{err.error}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}
          
          {/* Warning */}
          <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
            <div className="flex gap-3">
              <AlertCircle className="w-5 h-5 text-yellow-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-yellow-800">
                <p className="font-medium mb-1">Before you migrate:</p>
                <ul className="space-y-1">
                  <li>• A backup of your data will be created automatically</li>
                  <li>• The backend server must be running</li>
                  <li>• This process typically takes 10-30 seconds</li>
                  <li>• You can rollback if something goes wrong</li>
                </ul>
              </div>
            </div>
          </div>
        </div>
        
        {/* Actions */}
        <div className="flex items-center justify-between gap-3 p-6 bg-gray-50 border-t">
          <button
            onClick={handleSkip}
            className="px-4 py-2 text-gray-700 hover:bg-gray-200 rounded-lg transition-colors"
            disabled={migrating}
          >
            Remind me later
          </button>
          
          <div className="flex gap-3">
            {result && !result.success && (
              <button
                onClick={handleRollback}
                className="px-4 py-2 text-red-600 hover:bg-red-50 border border-red-200 rounded-lg transition-colors"
              >
                Rollback
              </button>
            )}
            
            {!result && (
              <button
                onClick={handleDryRun}
                disabled={migrating}
                className="px-4 py-2 text-blue-600 hover:bg-blue-50 border border-blue-200 rounded-lg transition-colors disabled:opacity-50"
              >
                Test Migration
              </button>
            )}
            
            {!result?.success && (
              <button
                onClick={handleMigrate}
                disabled={migrating}
                className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors disabled:opacity-50 font-medium"
              >
                {migrating ? 'Migrating...' : 'Migrate Now'}
              </button>
            )}
          </div>
        </div>
        
        {/* Details (if dry run) */}
        {showDetails && result && (
          <div className="p-6 border-t bg-gray-50">
            <h4 className="font-medium text-gray-900 mb-2">
              Migration Preview (Dry Run)
            </h4>
            <div className="text-sm text-gray-700 space-y-1">
              <p>✓ Would migrate {result.programsMigrated} programs</p>
              <p>✓ Would upload {result.imagesMigrated} images</p>
              <p>✓ Would create backup: {result.backupCreated ? 'Yes' : 'No'}</p>
              {result.errors.length > 0 && (
                <p className="text-red-600">⚠ {result.errors.length} potential issues detected</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default MigrationDialog;
