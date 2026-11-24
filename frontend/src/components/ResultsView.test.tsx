import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import ResultsView from './ResultsView'
import type { JobDetail, ProgressEvent } from '../types'

const baseDetail: JobDetail = {
  job_id: 'j1',
  input_type: 'text',
  status: 'queued',
  assets: [],
  lyrics_sections: [],
}

function renderResults(props: Partial<Parameters<typeof ResultsView>[0]>) {
  return render(
    <ResultsView
      jobId={props.jobId ?? 'j1'}
      detail={props.detail ?? null}
      events={props.events ?? []}
      lastEvent={props.lastEvent ?? null}
      onNewSong={props.onNewSong ?? vi.fn()}
      assetUrl={props.assetUrl ?? ((f) => `/v1/jobs/j1/files/${f}`)}
    />
  )
}

describe('ResultsView', () => {
  it('shows queued state with progress bar', () => {
    renderResults({ detail: { ...baseDetail, status: 'queued' } })
    expect(screen.getByText('正在生成…')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toBeInTheDocument()
    expect(screen.getByText('排队中')).toBeInTheDocument()
  })

  it('shows succeeded state with assets and lyrics', () => {
    const detail: JobDetail = {
      ...baseDetail,
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
          size_bytes: 1536,
        },
        {
          filename: 'lyrics.txt',
          label: '歌词文本',
          url: '/v1/jobs/j1/files/lyrics.txt',
          size_bytes: 512,
        },
      ],
      lyrics_sections: [
        { section: 'verse', lines: ['第一行歌词', '第二行歌词'] },
        { section: 'chorus', lines: ['副歌第一行'] },
      ],
    }
    renderResults({ detail })

    expect(screen.getByText('你的歌完成了 🎉')).toBeInTheDocument()
    expect(screen.getByText('调性 C')).toBeInTheDocument()
    expect(screen.getByText('BPM 90')).toBeInTheDocument()
    expect(screen.getByText('本次成本 $0.16')).toBeInTheDocument()

    const masterLink = screen.getByText('Master MP3').closest('a')
    expect(masterLink).toHaveAttribute('href', '/v1/jobs/j1/files/master.mp3')
    expect(masterLink).toHaveAttribute('download', 'master.mp3')

    expect(screen.getByText('VERSE')).toBeInTheDocument()
    expect(screen.getByText('第一行歌词')).toBeInTheDocument()
    expect(screen.getByText('副歌第一行')).toBeInTheDocument()
  })

  it('shows failed state with error and retry button', () => {
    const detail: JobDetail = {
      ...baseDetail,
      status: 'failed',
      error: '模型返回无音频',
      assets: [],
      lyrics_sections: [],
    }
    renderResults({ detail })

    expect(screen.getByText('生成失败')).toBeInTheDocument()
    expect(screen.getByText('模型返回无音频')).toBeInTheDocument()
    expect(screen.getByText('再试一次')).toBeInTheDocument()
    // 终止状态不应显示“新建一首”
    expect(screen.queryByText('新建一首')).not.toBeInTheDocument()
  })

  it('shows preview_ready hook preview', () => {
    const detail: JobDetail = {
      ...baseDetail,
      status: 'preview_ready',
      assets: [],
      lyrics_sections: [],
    }
    const lastEvent: ProgressEvent = {
      job_id: 'j1',
      type: 'preview_ready',
      stage: 'preview',
      preview_url: '/v1/jobs/j1/files/preview.wav',
    }
    renderResults({ detail, lastEvent })

    expect(screen.getByText('钩子预览已就绪')).toBeInTheDocument()
    expect(screen.getByText('⚡ 钩子预览')).toBeInTheDocument()
  })

  it('renders activity log from events', () => {
    const events: ProgressEvent[] = [
      { job_id: 'j1', type: 'stage_done', stage: 'intent', detail: '解析完成' },
      { job_id: 'j1', type: 'stage_done', stage: 'compose', error: 'retry 1' },
    ]
    renderResults({ detail: { ...baseDetail, status: 'running' }, events })

    expect(screen.getByText('活动日志（2）')).toBeInTheDocument()
    expect(screen.getByText('stage_done')).toBeInTheDocument()
    expect(screen.getByText('intent')).toBeInTheDocument()
    expect(screen.getByText('解析完成')).toBeInTheDocument()
    expect(screen.getByText('retry 1')).toBeInTheDocument()
  })
})