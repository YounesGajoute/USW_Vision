/**
 * Resolve REST API base URL for browser and SSR.
 * - Default: same-origin `/api` (Next.js proxy → Flask on INTERNAL_BACKEND_URL).
 * - Override: NEXT_PUBLIC_API_URL (e.g. master dashboard → http://192.168.10.2:5000/api).
 */
export function resolveApiBaseUrl(): string {
  const envUrl =
    typeof process !== 'undefined'
      ? process.env.NEXT_PUBLIC_API_URL?.trim()
      : undefined;

  if (envUrl) {
    const raw = envUrl.replace(/\/$/, '');
    if (raw.toLowerCase().endsWith('/api/v1')) return raw;
    if (raw.toLowerCase().endsWith('/api')) return raw;
    return `${raw}/api`;
  }

  if (typeof window !== 'undefined') {
    return `${window.location.origin}/api`;
  }

  return '/api';
}

export function formatFetchError(error: unknown, endpoint: string): string {
  if (error instanceof Error) {
    const msg = error.message || 'Unknown error';
    if (msg === 'Failed to fetch' || msg.includes('NetworkError') || msg.includes('Load failed')) {
      const base = resolveApiBaseUrl();
      return (
        `Cannot reach the vision API (${base}${endpoint}). ` +
        'Check that inspection-vision is running (ports 3000 + 5000 on the vision Pi). ' +
        'If you use the US Machine master UI, ensure it proxies DELETE /programs/:id to the vision Pi ' +
        'or set NEXT_PUBLIC_API_URL to http://<vision-ip>:5000/api.'
      );
    }
    if (error.name === 'AbortError' || msg.includes('aborted')) {
      return 'Request timed out — the server may still be deleting large image history. Refresh the program list.';
    }
    return msg;
  }
  return 'Please try again.';
}
