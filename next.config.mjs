import { existsSync, readFileSync } from 'fs'
import { dirname, join } from 'path'
import { fileURLToPath } from 'url'

/** @type {import('next').NextConfig} */
// Flask/Socket.IO on the same machine as Next (Docker Compose: set INTERNAL_BACKEND_URL=http://backend:5000)
const INTERNAL_BACKEND =
  process.env.INTERNAL_BACKEND_URL || 'http://127.0.0.1:5000'

const repoRoot = dirname(fileURLToPath(import.meta.url))

/** Pick up VISION_REMOTE_API_KEY for browser Socket.IO when backend/.env uses inherit auth. */
function loadSocketIoKeyFromBackendEnv() {
  if (process.env.NEXT_PUBLIC_VISION_SOCKETIO_KEY) return
  const envPath = join(repoRoot, 'backend', '.env')
  if (!existsSync(envPath)) return
  for (const line of readFileSync(envPath, 'utf8').split('\n')) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) continue
    const eq = trimmed.indexOf('=')
    if (eq < 1) continue
    const key = trimmed.slice(0, eq).trim()
    if (key !== 'VISION_REMOTE_API_KEY') continue
    let val = trimmed.slice(eq + 1).trim()
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1)
    }
    if (val) process.env.NEXT_PUBLIC_VISION_SOCKETIO_KEY = val
    break
  }
}

loadSocketIoKeyFromBackendEnv()

const nextConfig = {
  eslint: {
    ignoreDuringBuilds: true,
  },
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
  async rewrites() {
    const b = INTERNAL_BACKEND.replace(/\/$/, '')
    return [
      {
        source: '/api/:path*',
        destination: `${b}/api/:path*`,
      },
      // Backend engineio only matches PATH_INFO starting with '/socket.io/' (trailing slash)
      { source: '/socket.io', destination: `${b}/socket.io/` },
      { source: '/socket.io/', destination: `${b}/socket.io/` },
      {
        source: '/socket.io/:path*',
        destination: `${b}/socket.io/:path*`,
      },
    ]
  },
}

export default nextConfig
