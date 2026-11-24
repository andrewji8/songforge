/**
 * SongForge API client.
 *
 * Talks to the backend's /v1 REST surface + /ws progress stream. In dev
 * Vite proxies /v1 and /ws to :8000; in prod set VITE_API_BASE.
 */
import type {
  Asset,
  CreateJobInput,
  CreateJobResponse,
  JobDetail,
  ProgressEvent,
  SubscribeHandle,
} from './types'

const BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/+$/, '')
// Optional API key. REST routes expect it in the X-API-Key header; the
// WebSocket handshake (which cannot carry custom headers) uses ?api_key=.
const API_KEY = import.meta.env.VITE_API_KEY ?? ''

function authHeaders(extra: Record<string, string> = {}) {
  return API_KEY ? { ...extra, 'X-API-Key': API_KEY } : extra
}

export class ApiError extends Error {
  readonly status?: number
  constructor(message: string, status?: number) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail: string
    try {
      const body = await res.json()
      detail = (body as { detail?: string }).detail ?? JSON.stringify(body)
    } catch {
      detail = res.statusText
    }
    throw new ApiError(detail, res.status)
  }
  return res.json() as Promise<T>
}

export async function createTextJob(
  input: Omit<CreateJobInput, 'kind' | 'file'>
): Promise<CreateJobResponse> {
  const res = await fetch(`${BASE}/v1/jobs`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      description: input.description,
      input_type: 'text',
      duration_secs: input.durationSecs,
      style_hints: input.styleHints,
      language: input.language,
      lyrics: input.lyrics ?? null,
    }),
  })
  return parseJson(res)
}

async function createAudioJob(
  kind: 'voice' | 'loop',
  file: File,
  meta: Omit<CreateJobInput, 'kind' | 'file'>
): Promise<CreateJobResponse> {
  const params = new URLSearchParams({
    description: meta.description ?? '',
    style_hints: JSON.stringify(meta.styleHints ?? []),
    duration_secs: String(meta.durationSecs ?? 190),
    language: meta.language ?? 'zh',
  })
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`${BASE}/v1/jobs/${kind}?${params}`, {
    method: 'POST',
    headers: authHeaders(),
    body: fd,
  })
  return parseJson(res)
}

export const createVoiceJob = (
  file: File,
  meta: Omit<CreateJobInput, 'kind' | 'file'>
) => createAudioJob('voice', file, meta)

export const createLoopJob = (
  file: File,
  meta: Omit<CreateJobInput, 'kind' | 'file'>
) => createAudioJob('loop', file, meta)

export async function getJob(jobId: string): Promise<JobDetail> {
  const res = await fetch(`${BASE}/v1/jobs/${encodeURIComponent(jobId)}`, {
    headers: authHeaders(),
  })
  return parseJson(res)
}

export async function cancelJob(jobId: string): Promise<{
  ok: boolean
  status: string
}> {
  const res = await fetch(`${BASE}/v1/jobs/${jobId}/cancel`, {
    method: 'POST',
    headers: authHeaders(),
  })
  return parseJson(res)
}

export function assetUrl(
  base: string,
  jobDetail: JobDetail,
  filename: string
): string | null {
  const a = (jobDetail.assets ?? []).find((x) => x.filename === filename)
  return a ? `${base}${a.url}` : null
}

/**
 * Open the progress WebSocket for a job.
 */
export function subscribeJob(
  jobId: string,
  onEvent: (ev: ProgressEvent) => void
): SubscribeHandle {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const host =
    import.meta.env.VITE_WS_BASE ||
    `${window.location.host}` // dev: vite proxy /ws; prod: same origin
  const auth = API_KEY ? `?api_key=${encodeURIComponent(API_KEY)}` : ''
  const url = `${proto}://${host}/ws/jobs/${encodeURIComponent(jobId)}${auth}`
  const ws = new WebSocket(url)
  let closed = false

  ws.onmessage = (m) => {
    try {
      onEvent(JSON.parse(m.data) as ProgressEvent)
    } catch {
      /* ignore malformed frame */
    }
  }
  ws.onclose = () => {
    closed = true
  }
  ws.onerror = () => {
    /* reconnection is the caller's policy */
  }

  return {
    close: () => ws.close(),
    closed: () => closed,
  }
}