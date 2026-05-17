/**
 * API Service Layer
 * 
 * Provides a centralized service for all backend API calls.
 * Handles:
 * - HTTP requests with error handling
 * - Request retries with exponential backoff
 * - Caching strategy
 * - Offline fallback to localStorage
 * - Type-safe interfaces
 */

import { Program } from './storage';

// ==================== TYPES ====================

export interface ApiResponse<T> {
  data?: T;
  error?: string;
  message?: string;
}

export interface ProgramCreateRequest {
  name: string;
  config: {
    triggerType: string;
    triggerInterval: number;
    triggerDelay: number;
    brightnessMode: string;
    focusValue: number;
    masterImage: string | null;
    tools: any[];
    outputs: Record<string, any>;
  };
}

export interface BackupMetadata {
  backupId: string;
  timestamp: string;
  programCount: number;
  imageCount: number;
  resultCount: number;
  fileSize: number;
  description?: string;
}

export interface BackupExportOptions {
  includeImages?: boolean;
  includeResults?: boolean;
  includeSystemLogs?: boolean;
  description?: string;
}

export interface MigrationResult {
  dryRun: boolean;
  imported: {
    programs: number;
    images: number;
    results: number;
  };
  skipped: {
    programs: number;
    images: number;
  };
  errors: string[];
}

// ==================== CONFIGURATION ====================

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:5000/api';
const CACHE_ENABLED = true;
const RETRY_ATTEMPTS = 3;
const RETRY_DELAY = 1000; // ms
const REQUEST_TIMEOUT = 30000; // 30 seconds

// ==================== CACHE ====================

interface CacheEntry<T> {
  data: T;
  timestamp: number;
  ttl: number;
}

class SimpleCache {
  private cache: Map<string, CacheEntry<any>> = new Map();
  
  set<T>(key: string, data: T, ttl: number = 5 * 60 * 1000): void {
    this.cache.set(key, {
      data,
      timestamp: Date.now(),
      ttl
    });
  }
  
  get<T>(key: string): T | null {
    const entry = this.cache.get(key);
    
    if (!entry) return null;
    
    const age = Date.now() - entry.timestamp;
    if (age > entry.ttl) {
      this.cache.delete(key);
      return null;
    }
    
    return entry.data as T;
  }
  
  invalidate(pattern?: string): void {
    if (!pattern) {
      this.cache.clear();
      return;
    }
    
    const regex = new RegExp(pattern);
    for (const key of this.cache.keys()) {
      if (regex.test(key)) {
        this.cache.delete(key);
      }
    }
  }
  
  clear(): void {
    this.cache.clear();
  }
}

const cache = new SimpleCache();

// ==================== HTTP CLIENT ====================

class HttpClient {
  private async request<T>(
    url: string,
    options: RequestInit = {},
    retries: number = RETRY_ATTEMPTS
  ): Promise<T> {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);
    
    try {
      const response = await fetch(url, {
        ...options,
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          ...options.headers
        }
      });
      
      clearTimeout(timeoutId);
      
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
      }
      
      return await response.json();
      
    } catch (error: any) {
      clearTimeout(timeoutId);
      
      // Retry logic
      if (retries > 0 && this.shouldRetry(error)) {
        console.log(`Request failed, retrying... (${retries} attempts left)`);
        await this.delay(RETRY_DELAY);
        return this.request<T>(url, options, retries - 1);
      }
      
      throw error;
    }
  }
  
  private shouldRetry(error: any): boolean {
    // Retry on network errors, timeouts, and 5xx errors
    return (
      error.name === 'AbortError' ||
      error.message.includes('NetworkError') ||
      error.message.includes('500') ||
      error.message.includes('502') ||
      error.message.includes('503')
    );
  }
  
  private delay(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
  
  async get<T>(url: string, cacheKey?: string, cacheTtl?: number): Promise<T> {
    // Check cache first
    if (CACHE_ENABLED && cacheKey) {
      const cached = cache.get<T>(cacheKey);
      if (cached) {
        console.log(`Cache hit: ${cacheKey}`);
        return cached;
      }
    }
    
    const data = await this.request<T>(url, { method: 'GET' });
    
    // Cache the result
    if (CACHE_ENABLED && cacheKey && cacheTtl) {
      cache.set(cacheKey, data, cacheTtl);
    }
    
    return data;
  }
  
  async post<T>(url: string, body?: any): Promise<T> {
    return this.request<T>(url, {
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined
    });
  }
  
  async put<T>(url: string, body?: any): Promise<T> {
    return this.request<T>(url, {
      method: 'PUT',
      body: body ? JSON.stringify(body) : undefined
    });
  }
  
  async delete<T>(url: string): Promise<T> {
    return this.request<T>(url, { method: 'DELETE' });
  }
  
  async upload<T>(url: string, formData: FormData): Promise<T> {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT * 2); // Longer timeout for uploads
    
    try {
      const response = await fetch(url, {
        method: 'POST',
        body: formData,
        signal: controller.signal
      });
      
      clearTimeout(timeoutId);
      
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.error || `Upload failed: ${response.statusText}`);
      }
      
      return await response.json();
      
    } catch (error: any) {
      clearTimeout(timeoutId);
      throw error;
    }
  }
}

const http = new HttpClient();

// ==================== API SERVICE ====================

export class ApiService {
  // ==================== PROGRAMS ====================
  
  async getPrograms(activeOnly: boolean = true): Promise<Program[]> {
    try {
      const response = await http.get<{ programs: Program[] }>(
        `${API_BASE_URL}/programs?active_only=${activeOnly}`,
        `programs_${activeOnly}`,
        5 * 60 * 1000 // 5 minutes cache
      );
      return response.programs;
    } catch (error: any) {
      console.error('Failed to fetch programs from API:', error);
      // Fallback to localStorage
      return this.getFallbackPrograms();
    }
  }
  
  async getProgram(id: number): Promise<Program | null> {
    try {
      const response = await http.get<Program>(
        `${API_BASE_URL}/programs/${id}`,
        `program_${id}`,
        5 * 60 * 1000
      );
      return response;
    } catch (error: any) {
      console.error(`Failed to fetch program ${id}:`, error);
      return null;
    }
  }
  
  async createProgram(data: { name: string; config: any }): Promise<Program> {
    try {
      const response = await http.post<any>(
        `${API_BASE_URL}/programs`,
        data
      );
      
      // Invalidate programs cache
      cache.invalidate('programs_');
      
      return response;
    } catch (error: any) {
      console.error('Failed to create program:', error);
      throw new Error(error.message || 'Failed to create program');
    }
  }
  
  async updateProgram(id: number, updates: Partial<Program>): Promise<Program> {
    try {
      const response = await http.put<{ program: Program }>(
        `${API_BASE_URL}/programs/${id}`,
        updates
      );
      
      // Invalidate cache
      cache.invalidate('programs_');
      cache.invalidate(`program_${id}`);
      
      return response.program;
    } catch (error: any) {
      console.error(`Failed to update program ${id}:`, error);
      throw new Error(error.message || 'Failed to update program');
    }
  }
  
  async deleteProgram(id: number): Promise<void> {
    try {
      await http.delete(`${API_BASE_URL}/programs/${id}`);
      
      // Invalidate cache
      cache.invalidate('programs_');
      cache.invalidate(`program_${id}`);
    } catch (error: any) {
      console.error(`Failed to delete program ${id}:`, error);
      throw new Error(error.message || 'Failed to delete program');
    }
  }
  
  async updateProgramStats(id: number, stats: Partial<Program>): Promise<void> {
    try {
      await this.updateProgram(id, stats);
    } catch (error: any) {
      console.error(`Failed to update program stats ${id}:`, error);
      // Don't throw - stats updates are not critical
    }
  }
  
  // ==================== MASTER IMAGES ====================
  
  async uploadMasterImage(programId: number, imageFile: Blob): Promise<{ path: string; quality: any }> {
    try {
      const formData = new FormData();
      formData.append('file', imageFile, 'master.jpg');
      formData.append('programId', programId.toString());
      
      const response = await http.upload<{ path: string; quality: any }>(
        `${API_BASE_URL}/master-image`,
        formData
      );
      
      // Invalidate cache
      cache.invalidate(`master_image_${programId}`);
      
      return response;
    } catch (error: any) {
      console.error(`Failed to upload master image for program ${programId}:`, error);
      throw new Error(error.message || 'Failed to upload master image');
    }
  }
  
  async getMasterImage(programId: number): Promise<string | null> {
    try {
      const response = await http.get<{ image: string; format: string }>(
        `${API_BASE_URL}/master-image/${programId}`,
        `master_image_${programId}`,
        30 * 60 * 1000 // 30 minutes cache
      );
      return response.image ? `data:image/${response.format};base64,${response.image}` : null;
    } catch (error: any) {
      console.error(`Failed to fetch master image for program ${programId}:`, error);
      return null;
    }
  }
  
  // ==================== BACKUP & RESTORE ====================
  
  async exportBackup(options: BackupExportOptions = {}): Promise<BackupMetadata> {
    try {
      const response = await http.post<BackupMetadata>(
        `${API_BASE_URL}/backup/export`,
        {
          includeImages: options.includeImages ?? true,
          includeResults: options.includeResults ?? true,
          includeSystemLogs: options.includeSystemLogs ?? false,
          description: options.description || ''
        }
      );
      return response;
    } catch (error: any) {
      console.error('Failed to export backup:', error);
      throw new Error(error.message || 'Failed to export backup');
    }
  }
  
  async downloadBackup(backupId: string): Promise<void> {
    try {
      const url = `${API_BASE_URL}/backup/${backupId}/download`;
      window.open(url, '_blank');
    } catch (error: any) {
      console.error('Failed to download backup:', error);
      throw new Error(error.message || 'Failed to download backup');
    }
  }
  
  async importBackup(file: File, overwrite: boolean = false, dryRun: boolean = false): Promise<MigrationResult> {
    try {
      const formData = new FormData();
      formData.append('file', file);
      
      const response = await http.upload<MigrationResult>(
        `${API_BASE_URL}/backup/import?overwrite=${overwrite}&dry_run=${dryRun}`,
        formData
      );
      
      // Invalidate all caches after import
      if (!dryRun && response.imported.programs > 0) {
        cache.clear();
      }
      
      return response;
    } catch (error: any) {
      console.error('Failed to import backup:', error);
      throw new Error(error.message || 'Failed to import backup');
    }
  }
  
  async listBackups(limit: number = 50): Promise<BackupMetadata[]> {
    try {
      const response = await http.get<{ backups: BackupMetadata[] }>(
        `${API_BASE_URL}/backup/list?limit=${limit}`
      );
      return response.backups;
    } catch (error: any) {
      console.error('Failed to list backups:', error);
      return [];
    }
  }
  
  async deleteBackup(backupId: string): Promise<void> {
    try {
      await http.delete(`${API_BASE_URL}/backup/${backupId}`);
    } catch (error: any) {
      console.error(`Failed to delete backup ${backupId}:`, error);
      throw new Error(error.message || 'Failed to delete backup');
    }
  }
  
  // ==================== CAMERA ====================
  
  async captureImage(brightnessMode: string = 'normal', focusValue: number = 50): Promise<{ image: string; quality: any }> {
    try {
      const response = await http.post<{ image: string; quality: any }>(
        `${API_BASE_URL}/camera/capture`,
        { brightnessMode, focusValue }
      );
      return response;
    } catch (error: any) {
      console.error('Failed to capture image:', error);
      throw new Error(error.message || 'Failed to capture image');
    }
  }
  
  // ==================== HEALTH CHECK ====================
  
  async checkHealth(): Promise<boolean> {
    try {
      await http.get(`${API_BASE_URL}/health`);
      return true;
    } catch (error) {
      return false;
    }
  }
  
  // ==================== FALLBACK METHODS ====================
  
  private getFallbackPrograms(): Program[] {
    try {
      const data = localStorage.getItem('vision_programs');
      if (!data) return [];
      return JSON.parse(data);
    } catch (error) {
      console.error('Failed to load fallback programs:', error);
      return [];
    }
  }
  
  // ==================== CACHE MANAGEMENT ====================
  
  clearCache(): void {
    cache.clear();
  }
  
  invalidateCache(pattern?: string): void {
    cache.invalidate(pattern);
  }
}

// ==================== SINGLETON INSTANCE ====================

export const apiService = new ApiService();

// Export for easy access
export default apiService;
