import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from './App'
import type { JobDetail } from './types'

const originalFetch = globalThis.fetch
const mockFetch = vi.fn() as ReturnType<typeof vi.fn> & typeof fetch

beforeEach(() => {
  globalThis.fetch = mockFetch
  mockFetch.mockClear()
  vi.stubGlobal('alert', vi.fn())
})

afterEach(() => {
  globalThis.fetch = originalFetch
  vi.unstubAllGlobals()
})

describe('App', () => {
  it('runs a full text job flow end to end', async () => {
    // 1) createTextJob -> returns job_id
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ job_id: 'j1', status: 'queued' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    )
    // 2+) polling getJob -> eventually succeeded
    const detail: JobDetail = {
      job_id: 'j1',
      input_type: 'text',
      status: 'succeeded',
      key: 'C',
      bpm: 90,
      duration_secs: 190,
      cost_usd: 0.16,
      assets: [
        {
          filename: 'master.mp3',
          label: 'Master MP3',
          url: '/v1/jobs/j1/files/master.mp3',
          size_bytes: 1024,
        },
      ],
      lyrics_sections: [],
    }
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify(detail), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    )

    render(<App />)

    const desc = screen.getByLabelText(/描述你想要的歌曲/)
    fireEvent.change(desc, { target: { value: '测试歌曲，LoFi 风格' } })
    fireEvent.click(screen.getByText('生成歌曲'))

    // 应立即切换到结果页面并展示“正在生成”
    expect(screen.getByText('正在生成…')).toBeInTheDocument()

    // 轮询每 2.5s 调用 getJob，终到 succeeded
    await waitFor(() => {
      expect(screen.getByText('你的歌完成了 🎉')).toBeInTheDocument()
    }, { timeout: 5000 })

    expect(screen.getByText('调性 C')).toBeInTheDocument()
    expect(screen.getByText('BPM 90')).toBeInTheDocument()
    expect(screen.getByText('本次成本 $0.16')).toBeInTheDocument()
  })

  it('shows error alert when job creation fails', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'server error' }), {
        status: 500,
        headers: { 'content-type': 'application/json' },
      })
    )

    render(<App />)
    const desc = screen.getByLabelText(/描述你想要的歌曲/)
    fireEvent.change(desc, { target: { value: 'x' } })
    fireEvent.click(screen.getByText('生成歌曲'))

    // 描述太短，不会发请求，会展示校验错误
    expect(screen.getByText(/请至少输入 4 个字的描述/)).toBeInTheDocument()
  })
})