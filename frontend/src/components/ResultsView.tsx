import { useEffect, useMemo, useRef, useState } from 'react'
import type { JobDetail, ProgressEvent } from '../types'

const STAGE_LABELS: Record<string, string> = {
  intent: '解析意图',
  lyrics: '生成歌词',
  preview: '渲染钩子预览',
  compose: '生成全曲',
  mix_master: '混音 + 母带',
  export: '导出成品',
}

type ResultsViewProps = {
  jobId: string
  detail: JobDetail | null
  events: ProgressEvent[]
  lastEvent: ProgressEvent | null
  onNewSong: () => void
  assetUrl: (filename: string) => string
}

export default function ResultsView({
  jobId,
  detail,
  events,
  lastEvent,
  onNewSong,
  assetUrl,
}: ResultsViewProps) {
  const status = detail?.status ?? 'running'
  const terminal = ['succeeded', 'failed', 'cancelled'].includes(status)

  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const previewAudioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    if (lastEvent?.type === 'preview_ready' && lastEvent?.preview_url) {
      setPreviewUrl(
        lastEvent.preview_url.startsWith('http')
          ? lastEvent.preview_url
          : assetUrl('preview.wav')
      )
    }
  }, [lastEvent, assetUrl])

  useEffect(() => {
    if (detail?.status === 'succeeded') {
      const mp3 = detail.assets?.find((a) => a.filename === 'master.mp3')
      if (mp3) setPreviewUrl(mp3.url)
    }
  }, [detail])

  const progress = useMemo(() => {
    if (!detail) {
      const map: Record<string, number> = {
        preview: 0.3,
        compose: 0.6,
        mix_master: 0.8,
        export: 0.95,
      }
      const stage = lastEvent?.stage
      if (stage && map[stage] !== undefined) return map[stage]
      return 0.1
    }
    const map: Record<JobDetail['status'], number> = {
      queued: 0.05,
      running: 0.1,
      preview_ready: 0.35,
      succeeded: 1.0,
      failed: 1.0,
      cancelled: 1.0,
    }
    return map[status] ?? 0.1
  }, [detail, lastEvent, status])

  const currentStageLabel = (() => {
    if (status === 'succeeded') return '完成'
    if (status === 'failed') return '失败'
    if (status === 'cancelled') return '已取消'
    if (status === 'preview_ready') return '钩子预览已就绪'
    const s = lastEvent?.stage
    return s ? (STAGE_LABELS[s] ?? s) : '排队中'
  })()

  return (
    <section className="results">
      <div className="results-head">
        <div>
          <h2>
            {status === 'succeeded'
              ? '你的歌完成了 🎉'
              : status === 'failed'
              ? '生成失败'
              : '正在生成…'}
          </h2>
          <p className="sub">
            任务 <code>{jobId.slice(0, 8)}</code> · {currentStageLabel}
            {detail?.cost_usd > 0 && status === 'succeeded' && (
              <span> · 本次成本 ${detail.cost_usd.toFixed(2)}</span>
            )}
          </p>
        </div>
        {!terminal && (
          <button className="btn ghost" onClick={onNewSong}>
            新建一首
          </button>
        )}
      </div>

      <div className="progress" role="progressbar" aria-valuenow={Math.round(progress * 100)}>
        <div className="progress-fill" style={{ width: `${progress * 100}%` }} />
      </div>

      {status === 'preview_ready' && previewUrl && (
        <div className="hook-box">
          <h3>
            ⚡ 钩子预览（{Math.round(progress * 100)}% · 前奏副歌段）
          </h3>
          <audio
            ref={previewAudioRef}
            controls
            src={previewUrl}
            style={{ width: '100%' }}
          />
          <p className="hint">
            先听 30 秒钩子，全曲正在后台继续生成…
          </p>
        </div>
      )}

      {status === 'succeeded' && detail && (
        <>
          <div className="final-box">
            <h3>成品播放</h3>
            <audio
              controls
              src={assetUrl('master.mp3') ?? '#'}
              style={{ width: '100%' }}
            />
            <div className="meta">
              <span>调性 {detail.key ?? '—'}</span>
              <span>BPM {detail.bpm ?? '—'}</span>
              <span>
                时长 {Math.round((detail.duration_secs ?? 0) / 60 * 100) / 100} min
              </span>
            </div>
          </div>

          <div className="downloads">
            <h3>下载</h3>
            <div className="dl-grid">
              {detail.assets.map((a) => (
                <a
                  key={a.filename}
                  className="btn dl"
                  href={a.url ? assetUrl(a.filename) ?? '#' : '#'}
                  download={a.filename}
                >
                  {a.label}
                  <small>{fmtSize(a.size_bytes)}</small>
                </a>
              ))}
            </div>
          </div>

          {detail.lyrics_sections.length > 0 && (
            <div className="lyrics">
              <h3>歌词</h3>
              {detail.lyrics_sections.map((s, i) => (
                <div key={i} className="lyric-sec">
                  <div className="lyric-tag">{s.section.toUpperCase()}</div>
                  {s.lines.map((l, j) => (
                    <div key={j} className="lyric-line">{l}</div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {status === 'failed' && detail?.error && (
        <div className="error-box">
          <h3>出错了</h3>
          <p>{detail.error}</p>
          <button className="btn primary" onClick={onNewSong}>
            再试一次
          </button>
        </div>
      )}

      {events.length > 0 && (
        <details className="logbox">
          <summary>活动日志（{events.length}）</summary>
          <ul>
            {events.map((e, i) => (
              <li key={i}>
                <span className="log-type">{e.type}</span>
                {e.stage && <span> · {e.stage}</span>}
                {e.detail && <span> · {e.detail}</span>}
                {e.error && <span className="log-err"> · {e.error}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

function fmtSize(b: number | undefined | null) {
  if (!b) return ''
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${Math.round(b / 1024)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}