import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createTextJob,
  createVoiceJob,
  createLoopJob,
  getJob,
  subscribeJob,
} from './api'
import CreateView from './components/CreateView'
import ResultsView from './components/ResultsView'
import type { CreateJobInput, JobDetail, ProgressEvent } from './types'

const BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/+$/, '')

type View = 'create' | 'results'

export default function App() {
  const [view, setView] = useState<View>('create')
  const [jobId, setJobId] = useState<string | null>(null)
  const [detail, setDetail] = useState<JobDetail | null>(null)
  const [events, setEvents] = useState<ProgressEvent[]>([])
  const [lastEvent, setLastEvent] = useState<ProgressEvent | null>(null)
  const wsRef = useRef<ReturnType<typeof subscribeJob> | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const startTracking = useCallback(
    (id: string, onFinished: (d: JobDetail) => void) => {
      wsRef.current = subscribeJob(id, (ev) => {
        setEvents((prev) => [...prev, ev].slice(-50))
        setLastEvent(ev)
        if (['succeeded', 'failed', 'cancelled'].includes(ev.type)) {
          getJob(id)
            .then((d) => {
              setDetail(d)
              onFinished(d)
            })
            .catch(() => {})
          if (pollRef.current) {
            clearInterval(pollRef.current)
            pollRef.current = null
          }
        }
      })
      pollRef.current = setInterval(() => {
        getJob(id)
          .then((d) => {
            setDetail(d)
            if (['succeeded', 'failed', 'cancelled'].includes(d.status)) {
              onFinished(d)
              if (wsRef.current) {
                wsRef.current.close()
                wsRef.current = null
              }
              if (pollRef.current) {
                clearInterval(pollRef.current)
                pollRef.current = null
              }
            }
          })
          .catch(() => {})
      }, 2500)
    },
    []
  )

  const handleCreate = useCallback(
    async (payload: CreateJobInput) => {
      let created: { job_id: string }
      try {
        if (payload.kind === 'text') {
          created = await createTextJob(payload)
        } else if (payload.kind === 'voice') {
          created = await createVoiceJob(payload.file, payload)
        } else {
          created = await createLoopJob(payload.file, payload)
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        alert(`提交失败：${msg}`)
        return
      }
      setJobId(created.job_id)
      setDetail(null)
      setEvents([])
      setView('results')
      startTracking(created.job_id, () => {})
    },
    [startTracking]
  )

  const handleNewSong = () => {
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
    setJobId(null)
    setDetail(null)
    setEvents([])
    setLastEvent(null)
    setView('create')
  }

  useEffect(
    () => () => {
      if (wsRef.current) wsRef.current.close()
      if (pollRef.current) clearInterval(pollRef.current)
    },
    []
  )

  return (
    <div className="app">
      <header className="topbar">
        <button className="brand" onClick={handleNewSong}>
          <span className="brand-mark">♪</span> SongForge
        </button>
        <span className="tagline">一个念头，还你一首能直接发布的歌</span>
      </header>

      <main className="content">
        {view === 'create' ? (
          <CreateView onSubmit={handleCreate} />
        ) : (
          <ResultsView
            jobId={jobId ?? ''}
            detail={detail}
            events={events}
            lastEvent={lastEvent}
            onNewSong={handleNewSong}
            assetUrl={(filename) => `${BASE}/v1/jobs/${jobId}/files/${filename}`}
          />
        )}
      </main>

      <footer className="footer">
        SongForge · 可观测 / 可重试 / 可控成本 · 生成耗时取决于模型提供方
      </footer>
    </div>
  )
}