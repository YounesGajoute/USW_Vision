/**
 * IMX477 Camera API Client
 * Frontend integration for camera configuration and streaming
 */

const API_BASE_URL = process.env.NEXT_PUBLIC_CAMERA_API_URL || 'http://localhost:8000';

// ==================== Type Definitions ====================

export interface SensorConfig {
  lighting_mode: 'bright' | 'normal' | 'low' | 'astro';
  exposure_time: number;
  analog_gain: number;
  digital_gain: number;
  wb_mode: 'auto' | 'daylight' | 'manual';
  awb_red_gain?: number;
  awb_blue_gain?: number;
}

export interface DenoisingConfig {
  mode: 'none' | 'gaussian' | 'bilateral' | 'nlmeans' | 'temporal';
  h_parameter: number;
  template_size?: number;
  search_size?: number;
}

export interface CLAHEConfig {
  enabled: boolean;
  clip_limit: number;
  tile_size: number;
}

export interface SharpenConfig {
  enabled: boolean;
  amount: number;
  sigma: number;
  threshold?: number;
}

export interface OpenCVConfig {
  denoising: DenoisingConfig;
  clahe: CLAHEConfig;
  sharpen: SharpenConfig;
}

export interface PerformanceConfig {
  resolution: '480p' | '720p' | '1080p' | '4k';
  target_fps: 15 | 30 | 60;
  neon_enabled: boolean;
  dual_stream_enabled: boolean;
}

export interface CameraConfig {
  sensor: SensorConfig;
  opencv: OpenCVConfig;
  performance: PerformanceConfig;
}

export interface PerformanceMetrics {
  fps: number;
  avg_processing_time: number;
  cpu_usage: number;
  memory_usage: number;
  temperature: number;
  throttled: boolean;
}

export interface CameraStatus {
  initialized: boolean;
  streaming: boolean;
  sensor_config: SensorConfig | null;
  opencv_enabled: boolean;
  performance: PerformanceMetrics;
}

export interface ApiResponse<T = any> {
  success: boolean;
  message?: string;
  error?: string;
  data?: T;
}

// ==================== API Client ====================

export class IMX477API {
  private baseUrl: string;
  private metricsWebSocket: WebSocket | null = null;
  private metricsCallbacks: Set<(metrics: PerformanceMetrics) => void> = new Set();

  constructor(baseUrl: string = API_BASE_URL) {
    this.baseUrl = baseUrl;
  }

  // ==================== Configuration Endpoints ====================

  async configureSensor(config: SensorConfig): Promise<ApiResponse<{ iso_equivalent: number }>> {
    const response = await fetch(`${this.baseUrl}/api/camera/config/sensor`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    return response.json();
  }

  async configureOpenCV(config: OpenCVConfig): Promise<ApiResponse> {
    const response = await fetch(`${this.baseUrl}/api/camera/config/opencv`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    return response.json();
  }

  async configurePerformance(config: PerformanceConfig): Promise<ApiResponse> {
    const response = await fetch(`${this.baseUrl}/api/camera/config/performance`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    return response.json();
  }

  async configureComplete(config: CameraConfig): Promise<ApiResponse> {
    const response = await fetch(`${this.baseUrl}/api/camera/config/complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    });
    return response.json();
  }

  // ==================== Status & Metrics ====================

  async getMetrics(): Promise<PerformanceMetrics> {
    const response = await fetch(`${this.baseUrl}/api/camera/metrics`);
    return response.json();
  }

  async getStatus(): Promise<CameraStatus> {
    const response = await fetch(`${this.baseUrl}/api/camera/status`);
    return response.json();
  }

  // ==================== Camera Control ====================

  async startCamera(): Promise<ApiResponse> {
    const response = await fetch(`${this.baseUrl}/api/camera/start`, {
      method: 'POST',
    });
    return response.json();
  }

  async stopCamera(): Promise<ApiResponse> {
    const response = await fetch(`${this.baseUrl}/api/camera/stop`, {
      method: 'POST',
    });
    return response.json();
  }

  async captureImage(): Promise<ApiResponse<{ filename: string; resolution: [number, number] }>> {
    const response = await fetch(`${this.baseUrl}/api/camera/capture`, {
      method: 'POST',
    });
    return response.json();
  }

  // ==================== Presets ====================

  async getPresets(): Promise<Record<string, any>> {
    const response = await fetch(`${this.baseUrl}/api/camera/presets`);
    return response.json();
  }

  // ==================== Streaming ====================

  getStreamUrl(): string {
    return `${this.baseUrl}/api/camera/stream`;
  }

  // ==================== WebSocket Metrics ====================

  connectMetricsStream(callback: (metrics: PerformanceMetrics) => void): void {
    this.metricsCallbacks.add(callback);

    if (this.metricsWebSocket === null) {
      const wsUrl = this.baseUrl.replace('http://', 'ws://').replace('https://', 'wss://');
      this.metricsWebSocket = new WebSocket(`${wsUrl}/ws/camera/metrics`);

      this.metricsWebSocket.onopen = () => {
        console.log('📊 Metrics WebSocket connected');
      };

      this.metricsWebSocket.onmessage = (event) => {
        try {
          const metrics = JSON.parse(event.data);
          this.metricsCallbacks.forEach((cb) => cb(metrics));
        } catch (error) {
          console.error('Error parsing metrics:', error);
        }
      };

      this.metricsWebSocket.onerror = (error) => {
        console.error('❌ Metrics WebSocket error:', error);
      };

      this.metricsWebSocket.onclose = () => {
        console.log('📊 Metrics WebSocket closed');
        this.metricsWebSocket = null;
      };
    }
  }

  disconnectMetricsStream(callback?: (metrics: PerformanceMetrics) => void): void {
    if (callback) {
      this.metricsCallbacks.delete(callback);
    }

    if (this.metricsCallbacks.size === 0 && this.metricsWebSocket) {
      this.metricsWebSocket.close();
      this.metricsWebSocket = null;
    }
  }

  disconnectAllMetricsStreams(): void {
    this.metricsCallbacks.clear();
    if (this.metricsWebSocket) {
      this.metricsWebSocket.close();
      this.metricsWebSocket = null;
    }
  }
}

// ==================== Singleton Instance ====================

export const imx477API = new IMX477API();

// ==================== React Hook ====================

import { useEffect, useState } from 'react';

export function useIMX477Metrics(enabled: boolean = true) {
  const [metrics, setMetrics] = useState<PerformanceMetrics | null>(null);
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    if (!enabled) return;

    setIsConnected(true);
    imx477API.connectMetricsStream(setMetrics);

    return () => {
      imx477API.disconnectMetricsStream(setMetrics);
      setIsConnected(false);
    };
  }, [enabled]);

  return { metrics, isConnected };
}

export function useCameraStatus() {
  const [status, setStatus] = useState<CameraStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      setLoading(true);
      const data = await imx477API.getStatus();
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch status');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  return { status, loading, error, refresh };
}

// ==================== Helper Functions ====================

export function calculateISO(analogGain: number, digitalGain: number = 1.0): number {
  return Math.round(100 * analogGain * digitalGain);
}

export function estimateFPS(
  resolution: string,
  denoisingMode: string,
  neonEnabled: boolean,
  dualStreamEnabled: boolean
): number {
  let baseFps = 30;

  // Resolution adjustment
  if (resolution === '4k') baseFps = 10;
  else if (resolution === '720p') baseFps = 50;
  else if (resolution === '480p') baseFps = 60;

  // Denoising penalty
  const denoisingMultiplier: Record<string, number> = {
    none: 1.0,
    gaussian: 0.95,
    bilateral: 0.7,
    nlmeans: 0.1,
    temporal: 0.9,
  };
  baseFps *= denoisingMultiplier[denoisingMode] || 1.0;

  // Optimizations
  if (neonEnabled) baseFps *= 1.3;
  if (dualStreamEnabled && resolution !== '4k') baseFps *= 1.5;

  return Math.round(baseFps);
}

export function getSuggestedExposure(lightingMode: string) {
  const suggestions: Record<string, { min: number; max: number; suggested: number }> = {
    bright: { min: 125, max: 1000, suggested: 500 },
    normal: { min: 1000, max: 10000, suggested: 5000 },
    low: { min: 10000, max: 100000, suggested: 33333 },
    astro: { min: 100000, max: 5000000, suggested: 1000000 },
  };
  return suggestions[lightingMode] || suggestions.normal;
}

export function getSuggestedGain(lightingMode: string) {
  const suggestions: Record<string, { min: number; max: number; suggested: number }> = {
    bright: { min: 1.0, max: 2.0, suggested: 1.0 },
    normal: { min: 1.0, max: 4.0, suggested: 2.0 },
    low: { min: 4.0, max: 16.0, suggested: 8.0 },
    astro: { min: 8.0, max: 16.0, suggested: 16.0 },
  };
  return suggestions[lightingMode] || suggestions.normal;
}

