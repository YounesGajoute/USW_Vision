import type { ProgramConfig } from '@/types';

export interface Program {
  id: string;
  name: string;
  created: string;
  lastRun: string | null;
  totalInspections: number;
  okCount: number;
  ngCount: number;
  config: ProgramConfig;
}

export const storage = {
  saveProgram: (program: Program): void => {
    try {
      const programs = storage.getAllPrograms();
      const index = programs.findIndex(p => p.id === program.id);
      if (index >= 0) {
        programs[index] = program;
      } else {
        programs.push(program);
      }
      localStorage.setItem('vision_programs', JSON.stringify(programs));
    } catch (error) {
      console.error('Failed to save program:', error);
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
      console.error('Failed to load programs:', error);
      return [];
    }
  },

  getProgram: (id: string): Program | null => {
    try {
      const programs = storage.getAllPrograms();
      return programs.find(p => p.id === id) || null;
    } catch (error) {
      console.error('Failed to get program:', error);
      return null;
    }
  },

  deleteProgram: (id: string): void => {
    try {
      const programs = storage.getAllPrograms().filter(p => p.id !== id);
      localStorage.setItem('vision_programs', JSON.stringify(programs));
    } catch (error) {
      console.error('Failed to delete program:', error);
      throw new Error('Failed to delete program from storage');
    }
  },

  updateStats: (id: string, stats: Partial<Program>): void => {
    try {
      const program = storage.getProgram(id);
      if (program) {
        storage.saveProgram({ ...program, ...stats });
      } else {
        console.warn(`Program with id ${id} not found for stats update`);
      }
    } catch (error) {
      console.error('Failed to update program stats:', error);
      throw new Error('Failed to update program statistics');
    }
  }
};
