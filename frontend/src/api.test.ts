import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import {
  createTextJob,
  createVoiceJob,
  getJob,
  cancelJob,
  ApiError,
} from './api'
import type { CreateJobResponse, JobDetail } from './types'

const BASE = 'http://localhost:8000'

const mockFetch = vi.fn() as ReturnType<typeof vi.fn> & typeof fetch
const originalFetch = globalThis.fetch

beforeEach(() => {
  globalThis.fetch = mockFetch
  mockFetch.mockClear()
})

afterEach(() => {
  globalThis.fetch = originalFetch
  vi.unstubAllGlobals()
})

function okJson(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('api.createTextJob', () => {
  it('posts to /v1/jobs with text input_type', async () => {
    const resp: CreateJobResponse = { job_id: 'abc-123', status: 'queued' }
    mockFetch.mockResolvedValueOnce(okJson(resp))

    const data = await createTextJob({
      description: '深夜雨声里想念旧朋友',
      durationSecs: 90,
      language: 'zh',
      styleHints: ['LoFi'],
    })

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe(`${BASE}/v1/jobs`)
    expect(init?.method).toBe('POST')
    expect(init?.headers).toMatchObject({
      'Content-Type': 'application/json',
      'X-API-Key': undefined,
    })
    expect(data).toEqual(resp)
  })

  it('throws ApiError with status on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ detail: 'description too short' }, 422))

    await expect(
      createTextJob({
        description: 'ab',
        durationSecs: 90,
        language: 'zh',
        styleHints: [],
      })
    ).rejects.toMatchObject(new ApiError('description too short', 422))
  })
})

describe('api.createVoiceJob / createLoopJob', () => {
  it('posts multipart form to /v1/jobs/voice', async () => {
    const resp: CreateJobResponse = { job_id: 'voice-1', status: 'queued' }
    mockFetch.mockResolvedValueOnce(okJson(resp))

    const file = new File(['dummy'], 'voice.wav', { type: 'audio/wav' })
    await createVoiceJob(file, {
      description: '哼唱',
      durationSecs: 60,
      language: 'zh',
      styleHints: [],
    })

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toMatch(/\/v1\/jobs\/voice\?/)
    expect(init?.method).toBe('POST')
    expect(init?.body).toBeInstanceOf(FormData)
  })

  it('posts multipart form to /v1/jobs/loop', async () => {
    const resp: CreateJobResponse = { job_id: 'loop-1', status: 'queued' }
    mockFetch.mockResolvedValueOnce(okJson(resp))

    const file = new File(['dummy'], 'loop.wav', { type: 'audio/wav' })
    await createLoopJob(file, {
      description: '节拍',
      durationSecs: 90,
      language: 'zh',
      styleHints: ['EDM'],
    })

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url] = mockFetch.mock.calls[0]
    expect(url).toMatch(/\/v1\/jobs\/loop\?/)
  })
})

describe('api.getJob', () => {
  it('fetches job detail and returns typed JobDetail', async () => {
    const detail: JobDetail = {
      job_id: 'j1',
      input_type: 'text',
      status: 'succeeded',
      key: 'C',
      bpm: 90,
      duration_secs: 190,
      cost_usd: 0.16,
      assets: [
        { filename: 'master.mp3', label: 'Master MP3', url: '/v1/jobs/j1/files/master.mp3', size_bytes: 1024 },
      ],
      lyrics_sections: [],
    }
    mockFetch.mockResolvedValueOnce(okJson(detail))

    const data = await getJob('j1')
    expect(data).toEqual(detail)
    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url] = mockFetch.mock.calls[0]
    expect(url).toBe(`${BASE}/v1/jobs/j1`)
  })
})

describe('api.cancelJob', () => {
  it('posts to cancel endpoint', async () => {
    mockFetch.mockResolvedValueOnce(okJson({ ok: true, status: 'cancelled' }))

    const data = await cancelJob('j1')
    expect(data).toEqual({ ok: true, status: 'cancelled' })
    const [url, init] = mockFetch.mock.calls[0]
    expect(url).toBe(`${BASE}/v1/jobs/j1/cancel`)
    expect(init?.method).toBe('POST')
  })
})