/**
 * Storage Adapter
 * 
 * Unified storage interface that uses API service as primary storage
 * and falls back to localStorage when API is unavailable.
 * 
 * This provides a seamless transition from localStorage-based storage
 * to API-based storage without breaking existing code.
 */

import { Program } from './storage';
import { apiService } from './api-service';

// Track API availability
let apiAvailable: boolean | null = null;
let lastHealthCheck = 0;
const HEALTH_CHECK_INTERVAL = 30000; // 30 seconds

/**
 * Check if backend API is available
 */
async function checkApiAvailability(): Promise<boolean> {
  const now = Date.now();
  
  // Use cached result if recent
  if (apiAvailable !== null && (now - lastHealthCheck) < HEALTH_CHECK_INTERVAL) {
    return apiAvailable;
  }
  
  try {
    apiAvailable = await apiService.checkHealth();
    lastHealthCheck = now;
    return apiAvailable;
  } catch (error) {
    apiAvailable = false;
    lastHealthCheck = now;
    return false;
  }
}

/**
 * LocalStorage fallback methods
 */
const localStorageFallback = {
  saveProgram: (program: Program): void => {
    try {
      const programs = localStorageFallback.getAllPrograms();
      const index = programs.findIndex(p => p.id === program.id);
      if (index >= 0) {
        programs[index] = program;
      } else {
        programs.push(program);
      }
      localStorage.setItem('vision_programs', JSON.stringify(programs));
    } catch (error) {
      console.error('Failed to save program to localStorage:', error);
      throw new Error('Failed to save program to storage');
    }
  },

  getAllPrograms: (): Program[] => {
    try {
      const data = localStorage.getItem('vision_programs');
      if (!data) return [];
      
      const parsed = JSON.parse(data);
      if (!Array.isArray(parsed)) {
        console.warn('Invalid program data format, returning empty array');
        return [];
      }
      
      return parsed;
    } catch (error) {
      console.error('Failed to load programs from localStorage:', error);
      return [];
    }
  },

  getProgram: (id: string): Program | null => {
    try {
      const programs = localStorageFallback.getAllPrograms();
      return programs.find(p => p.id === id) || null;
    } catch (error) {
      console.error('Failed to get program from localStorage:', error);
      return null;
    }
  },

  deleteProgram: (id: string): void => {
    try {
      const programs = localStorageFallback.getAllPrograms().filter(p => p.id !== id);
      localStorage.setItem('vision_programs', JSON.stringify(programs));
    } catch (error) {
      console.error('Failed to delete program from localStorage:', error);
      throw new Error('Failed to delete program from storage');
    }
  },

  updateStats: (id: string, stats: Partial<Program>): void => {
    try {
      const program = localStorageFallback.getProgram(id);
      if (program) {
        localStorageFallback.saveProgram({ ...program, ...stats });
      } else {
        console.warn(`Program with id ${id} not found for stats update`);
      }
    } catch (error) {
      console.error('Failed to update program stats in localStorage:', error);
      throw new Error('Failed to update program statistics');
    }
  }
};

/**
 * Storage Adapter - API-first with localStorage fallback
 */
export const storageAdapter = {
  /**
   * Get all programs
   */
  getAllPrograms: async (): Promise<Program[]> => {
    const hasApi = await checkApiAvailability();
    
    if (hasApi) {
      try {
        return await apiService.getPrograms();
      } catch (error) {
        console.warn('API failed, falling back to localStorage:', error);
        return localStorageFallback.getAllPrograms();
      }
    } else {
      console.log('API unavailable, using localStorage');
      return localStorageFallback.getAllPrograms();
    }
  },

  /**
   * Get a single program by ID
   */
  getProgram: async (id: string): Promise<Program | null> => {
    const hasApi = await checkApiAvailability();
    
    if (hasApi) {
      try {
        return await apiService.getProgram(parseInt(id));
      } catch (error) {
        console.warn('API failed, falling back to localStorage:', error);
        return localStorageFallback.getProgram(id);
      }
    } else {
      return localStorageFallback.getProgram(id);
    }
  },

  /**
   * Save a program (create or update)
   */
  saveProgram: async (program: Program): Promise<void> => {
    const hasApi = await checkApiAvailability();
    
    if (hasApi) {
      try {
        // Check if program exists
        const existing = await apiService.getProgram(parseInt(program.id));
        
        if (existing) {
          // Update existing
          await apiService.updateProgram(parseInt(program.id), program);
        } else {
          // Create new
          await apiService.createProgram({
            name: program.name,
            config: program.config
          });
        }
      } catch (error) {
        console.warn('API failed, falling back to localStorage:', error);
        localStorageFallback.saveProgram(program);
      }
    } else {
      localStorageFallback.saveProgram(program);
    }
  },

  /**
   * Delete a program
   */
  deleteProgram: async (id: string): Promise<void> => {
    const hasApi = await checkApiAvailability();
    
    if (hasApi) {
      try {
        await apiService.deleteProgram(parseInt(id));
      } catch (error) {
        console.warn('API failed, falling back to localStorage:', error);
        localStorageFallback.deleteProgram(id);
      }
    } else {
      localStorageFallback.deleteProgram(id);
    }
  },

  /**
   * Update program statistics
   */
  updateStats: async (id: string, stats: Partial<Program>): Promise<void> => {
    const hasApi = await checkApiAvailability();
    
    if (hasApi) {
      try {
        await apiService.updateProgramStats(parseInt(id), stats);
      } catch (error) {
        console.warn('API failed, falling back to localStorage:', error);
        localStorageFallback.updateStats(id, stats);
      }
    } else {
      localStorageFallback.updateStats(id, stats);
    }
  },

  /**
   * Force refresh API availability check
   */
  refreshApiStatus: async (): Promise<boolean> => {
    apiAvailable = null;
    lastHealthCheck = 0;
    return await checkApiAvailability();
  },

  /**
   * Get current storage mode
   */
  getStorageMode: async (): Promise<'api' | 'localStorage'> => {
    const hasApi = await checkApiAvailability();
    return hasApi ? 'api' : 'localStorage';
  }
};

export default storageAdapter;
