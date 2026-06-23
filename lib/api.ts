/**
 * API Client for Vision Inspection System
 * Handles all REST API communication with backend
 */

import { formatFetchError, resolveApiBaseUrl } from '@/lib/api-base-url';
import type {
  Program,
  ProgramConfig,
  CaptureOptions,
  CapturedImage,
  CameraInfo,
  OptimizationResult,
  MasterImageUploadResponse,
  ProgramListResponse,
  HealthStatus,
  ToolResult,
  ToolConfig,
  ToolTemplate,
  ToolTemplateSummary,
  ToolTemplateListResponse,
  ToolTemplateForProgram,
} from '@/types';

class APIClient {
  private baseURL: string;
  /** Optional shared secret for Pi-to-Pi calls to /remote/* (matches slave `remote.api_key`). */
  visionRemoteKey?: string;

  constructor(baseURL?: string, visionRemoteKey?: string) {
    this.baseURL = baseURL ?? resolveApiBaseUrl();
    this.visionRemoteKey = visionRemoteKey;
  }

  /**
   * Generic request handler with error handling
   */
  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${this.baseURL}${endpoint}`;
    
    const defaultHeaders: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (this.visionRemoteKey) {
      defaultHeaders['X-Vision-Remote-Key'] = this.visionRemoteKey;
    }

    const config: RequestInit = {
      ...options,
      headers: {
        ...defaultHeaders,
        ...(options.headers as Record<string, string>),
      },
    };

    const timeoutMs =
      options.method === 'DELETE' ? 120_000 : 60_000;

    try {
      const response = await fetch(url, {
        ...config,
        signal: options.signal ?? AbortSignal.timeout(timeoutMs),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ error: response.statusText }));
        throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      console.error(`API request failed: ${endpoint}`, error);
      throw new Error(formatFetchError(error, endpoint));
    }
  }

  // ==================== PROGRAM ENDPOINTS ====================

  /**
   * Create a new inspection program
   */
  async createProgram(name: string, config: ProgramConfig): Promise<Program> {
    return this.request<Program>('/programs', {
      method: 'POST',
      body: JSON.stringify({ name, config }),
    });
  }

  /**
   * Get list of all programs
   */
  async getPrograms(activeOnly: boolean = true): Promise<Program[]> {
    const response = await this.request<ProgramListResponse>(
      `/programs?active_only=${activeOnly}`
    );
    return response.programs;
  }

  /**
   * Get a single program by ID
   */
  async getProgram(id: number): Promise<Program> {
    return this.request<Program>(`/programs/${id}`);
  }

  /**
   * Update an existing program
   */
  async updateProgram(id: number, updates: Partial<{ name: string; config: ProgramConfig }>): Promise<{ message: string; program: Program }> {
    return this.request(`/programs/${id}`, {
      method: 'PUT',
      body: JSON.stringify(updates),
    });
  }

  /**
   * Delete a program (soft delete)
   */
  async deleteProgram(id: number): Promise<{ message: string }> {
    return this.request(`/programs/${id}`, {
      method: 'DELETE',
    });
  }

  // ==================== TOOL TEMPLATE ENDPOINTS ====================

  async getToolTemplates(configuringProgramId?: number): Promise<ToolTemplateSummary[]> {
    const q =
      configuringProgramId != null ? `?program_id=${configuringProgramId}` : '';
    const response = await this.request<ToolTemplateListResponse>(`/tool-templates${q}`);
    return response.templates;
  }

  /** Save/update this program's private template (named like the program). */
  async upsertProgramToolTemplate(
    programId: number,
    data: {
      tools: ToolConfig[];
      description?: string;
      roi_space?: string;
    }
  ): Promise<{ message: string; template: ToolTemplate; toolTemplateId: number }> {
    return this.request(`/programs/${programId}/tool-template`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  async getToolTemplate(id: number, includeImage: boolean = true): Promise<ToolTemplate> {
    return this.request<ToolTemplate>(
      `/tool-templates/${id}?include_image=${includeImage ? 'true' : 'false'}`
    );
  }

  async createToolTemplate(data: {
    name: string;
    tools: ToolConfig[];
    description?: string;
    roi_space?: string;
  }): Promise<{ message: string; template: ToolTemplate }> {
    return this.request('/tool-templates', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  /**
   * Template tools with ROIs scaled to the program master (for inspection run / preview).
   */
  async getToolTemplateForProgram(
    templateId: number,
    programId: number
  ): Promise<ToolTemplateForProgram> {
    return this.request(`/tool-templates/${templateId}/for-program/${programId}`);
  }

  async deleteToolTemplate(id: number): Promise<{ message: string }> {
    return this.request(`/tool-templates/${id}`, {
      method: 'DELETE',
    });
  }

  // ==================== MASTER IMAGE ENDPOINTS ====================

  /**
   * Upload master image for a program
   */
  async uploadMasterImage(programId: number, file: File): Promise<MasterImageUploadResponse> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('programId', programId.toString());

    const url = `${this.baseURL}/master-image`;

    try {
      const response = await fetch(url, {
        method: 'POST',
        body: formData,
        // Don't set Content-Type header - browser will set it with boundary
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ error: response.statusText }));
        throw new Error(errorData.error || `Upload failed: ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      console.error('Master image upload failed', error);
      throw error;
    }
  }

  /**
   * Get master image for a program
   */
  async getMasterImage(programId: number): Promise<{ image: string; format: string }> {
    return this.request(`/master-image/${programId}`);
  }

  // ==================== CAMERA ENDPOINTS ====================

  /**
   * Get camera hardware info (model, resolution, interface, sensor type …)
   */
  async getCameraInfo(): Promise<CameraInfo> {
    return this.request<CameraInfo>('/camera/info');
  }

  /**
   * Capture a single image from camera.
   * Pass exposureTime (µs) and/or analogGain to override the brightnessMode preset.
   */
  async captureImage(options: CaptureOptions = {}): Promise<CapturedImage> {
    return this.request<CapturedImage>('/camera/capture', {
      method: 'POST',
      body: JSON.stringify(options),
    });
  }

  /**
   * Run one full server-side inspection (InspectionEngine: lighting, GPIO, same algorithms as WebSocket).
   * Persists to DB by default (same row as remote run-once).
   */
  async runInspectionOnce(body: {
    programId: number;
    triggerType?: string;
    includeImage?: boolean;
    persist?: boolean;
  }): Promise<{
    programId: number;
    programName: string;
    status: 'OK' | 'NG';
    toolResults: ToolResult[];
    processingTimeMs: number;
    resultId: number | null;
    triggerType: string;
    image?: string;
    imageFormat?: 'png' | 'jpg';
  }> {
    return this.request('/inspection/run-once', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  /**
   * Run inspection using a tool template on a program's registered master image.
   * Template ROIs (wizard space) are scaled to master dimensions server-side.
   */
  async runInspectionWithTemplate(body: {
    templateId: number;
    programId: number;
    triggerType?: string;
    includeImage?: boolean;
    persist?: boolean;
  }): Promise<{
    programId: number;
    programName: string;
    templateId: number;
    templateName?: string;
    runMode: 'template';
    status: 'OK' | 'NG';
    toolResults: ToolResult[];
    processingTimeMs: number;
    resultId: number | null;
    triggerType: string;
    image?: string;
    imageFormat?: 'png' | 'jpg';
    masterSize?: { width: number; height: number };
    toolCount?: number;
  }> {
    return this.request('/inspection/run-with-template', {
      method: 'POST',
      body: JSON.stringify({
        templateId: body.templateId,
        programId: body.programId,
        triggerType: body.triggerType,
        includeImage: body.includeImage,
        persist: body.persist,
      }),
    });
  }

  /**
   * Auto-optimize camera settings (brightness and sharpness baseline).
   * Fixed-focus sensors skip the focus sweep.
   */
  async autoOptimize(): Promise<OptimizationResult> {
    return this.request<OptimizationResult>('/camera/auto-optimize', {
      method: 'POST',
    });
  }

  /**
   * Start live camera preview
   */
  async startPreview(): Promise<{ message: string }> {
    return this.request('/camera/preview/start', {
      method: 'POST',
    });
  }

  /**
   * Stop live camera preview
   */
  async stopPreview(): Promise<{ message: string }> {
    return this.request('/camera/preview/stop', {
      method: 'POST',
    });
  }

  // ==================== GPIO ENDPOINTS ====================

  /**
   * Get current state of all GPIO outputs
   */
  async getGPIOOutputs(): Promise<{ outputs: Record<number, boolean> }> {
    return this.request('/gpio/outputs');
  }

  /**
   * Set a single GPIO output state
   */
  async setGPIOOutput(outputNumber: number, state: boolean): Promise<{ message: string }> {
    return this.request(`/gpio/outputs/${outputNumber}`, {
      method: 'POST',
      body: JSON.stringify({ state }),
    });
  }

  /**
   * Run GPIO test sequence
   */
  async testGPIO(): Promise<{ message: string }> {
    return this.request('/gpio/test', {
      method: 'POST',
    });
  }

  // ==================== INSPECTION LIGHTING (single GPIO or P9813) ====================

  async getLightingStatus(): Promise<{
    driver: string | null;
    ready: boolean;
    pins:
      | { clock: number; data: number; num_leds: number }
      | {
          pin: number;
          pwm: boolean;
          pwm_frequency: number;
          active_high?: boolean;
          solid_at_full?: boolean;
        }
      | null;
    settings: Record<string, unknown>;
  }> {
    return this.request('/lighting/status');
  }

  async setLightingRgb(r: number, g: number, b: number): Promise<{ message: string }> {
    return this.request('/lighting/rgb', {
      method: 'POST',
      body: JSON.stringify({ r, g, b }),
    });
  }

  async setLightingPixels(pixels: [number, number, number][]): Promise<{ message: string }> {
    return this.request('/lighting/rgb', {
      method: 'POST',
      body: JSON.stringify({ pixels }),
    });
  }

  async lightingOff(): Promise<{ message: string }> {
    return this.request('/lighting/off', { method: 'POST' });
  }

  // ==================== REMOTE MASTER / AGENT (same slave as REST) ====================

  async getRemoteAgentInfo(): Promise<{
    role: string;
    socketio_path: string;
    socketio_events: Record<string, unknown>;
    rest: Record<string, string>;
    remote_auth_required: boolean;
  }> {
    return this.request('/remote/info');
  }

  async runRemoteInspectionOnce(
    programId: number,
    options: { triggerType?: string; includeImage?: boolean } = {}
  ): Promise<{
    programId: number;
    programName: string;
    status: string;
    toolResults: unknown[];
    processingTimeMs: number;
    resultId: number;
    triggerType: string;
    image?: string;
    imageFormat?: 'png' | 'jpg';
  }> {
    return this.request('/remote/inspection/run-once', {
      method: 'POST',
      body: JSON.stringify({
        programId,
        triggerType: options.triggerType ?? 'remote',
        includeImage: options.includeImage !== false,
      }),
    });
  }

  // ==================== INSPECTION HISTORY ENDPOINTS ====================

  /**
   * Save an inspection result to the database
   */
  async logInspectionResult(data: {
    program_id: number;
    status: 'OK' | 'NG';
    processing_time_ms: number;
    tool_results: any[];
    trigger_type?: string;
    notes?: string;
  }): Promise<{ id: number; message: string }> {
    return this.request('/inspections', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  /**
   * Retrieve inspection history for a program
   */
  async getInspectionHistory(
    programId: number,
    options: { limit?: number; status?: 'OK' | 'NG' } = {}
  ): Promise<{ history: any[]; program_id: number; total: number }> {
    const params = new URLSearchParams();
    if (options.limit) params.set('limit', String(options.limit));
    if (options.status) params.set('status', options.status);
    const query = params.toString() ? `?${params.toString()}` : '';
    return this.request(`/inspections/${programId}${query}`);
  }

  /**
   * Returns the URL for a saved inspection image (for use in <img src=...>)
   */
  getInspectionImageUrl(programId: number, resultId: number): string {
    return `${this.baseURL}/inspections/${programId}/${resultId}/image`;
  }

  // ==================== HEALTH CHECK ====================

  /**
   * Get system health status
   */
  async healthCheck(): Promise<HealthStatus> {
    return this.request<HealthStatus>('/health');
  }
}

// Export singleton instance
export const api = new APIClient();

// Export class for testing or custom instances
export default APIClient;

