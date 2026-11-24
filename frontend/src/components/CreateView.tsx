import { useRef, useState } from 'react'
import type { CreateJobInput, JobMeta } from '../types'

const STYLE_PRESETS = ['LoFi', 'Pop', 'EDM', 'Acoustic', 'Cinematic', 'Hip-Hop']
const LANGS = [
  { id: 'zh', label: '中文' },
  { id: 'en', label: 'English' },
]

export default function CreateView({
  onSubmit,
}: {
  onSubmit: (input: CreateJobInput) => void
}) {
  const [tab, setTab] = useState<'text' | 'voice' | 'loop'>('text')
  const [description, setDescription] = useState('')
  const [duration, setDuration] = useState(190)
  const [language, setLanguage] = useState('zh')
  const [customLyrics, setCustomLyrics] = useState('')
  const [styleHints, setStyleHints] = useState<string[]>([])
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [recording, setRecording] = useState(false)
  const [recSeconds, setRecSeconds] = useState(0)
  const [recFile, setRecFile] = useState<File | null>(null)
  const [loopFile, setLoopFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [err, setErr] = useState('')

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const recChunksRef = useRef<Blob[]>([])
  const recTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const loopInputRef = useRef<HTMLInputElement | null>(null)

  const stopRecording = () => {
    const mr = mediaRecorderRef.current
    if (mr && mr.state !== 'inactive') mr.stop()
  }

  const startRecording = async () => {
    setErr('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const chunks: Blob[] = []
      const mr = new MediaRecorder(stream, {
        mimeType: pickAudioMime(),
      })
      mediaRecorderRef.current = mr
      recChunksRef.current = chunks
      mr.ondataavailable = (e) => e.data.size && chunks.push(e.data)
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        const blob = new Blob(chunks, { type: pickAudioMime() || 'audio/webm' })
        setRecFile(
          new File([blob], `recording-${Date.now()}.webm`, { type: blob.type })
        )
        setRecording(false)
        if (recTimerRef.current) clearInterval(recTimerRef.current)
      }
      mr.start()
      setRecording(true)
      setRecSeconds(0)
      recTimerRef.current = setInterval(() => setRecSeconds((s) => s + 1), 1000)
    } catch (e) {
      setErr('无法访问麦克风：' + (e as Error).message)
    }
  }

  const onLoopFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) setLoopFile(f)
  }

  const toggleStyle = (s: string) =>
    setStyleHints((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]
    )

  const submit = () => {
    setErr('')
    setSubmitting(true)
    const meta: JobMeta = {
      description,
      durationSecs: duration,
      language,
      styleHints,
      lyrics: customLyrics || null,
    }
    if (tab === 'text') {
      if (description.trim().length < 4) {
        setErr('请至少输入 4 个字的描述')
        setSubmitting(false)
        return
      }
      onSubmit({ kind: 'text', ...meta })
    } else if (tab === 'voice') {
      if (!recFile) {
        setErr('请先录一段你的声音（3–30 秒）')
        setSubmitting(false)
        return
      }
      onSubmit({ kind: 'voice', file: recFile, ...meta })
    } else {
      if (!loopFile) {
        setErr('请上传一段节拍 / loop')
        setSubmitting(false)
        return
      }
      onSubmit({ kind: 'loop', file: loopFile, ...meta })
    }
  }

  return (
    <section className="create">
      <h1>新建歌曲</h1>
      <p className="sub">
        一句话、一段人声或一个节拍，还你一首可发行的成品。
      </p>

      <div className="tabs">
        <button
          className={`tab ${tab === 'text' ? 'active' : ''}`}
          onClick={() => setTab('text')}
        >
          文本描述
        </button>
        <button
          className={`tab ${tab === 'voice' ? 'active' : ''}`}
          onClick={() => setTab('voice')}
        >
          人声录音
        </button>
        <button
          className={`tab ${tab === 'loop' ? 'active' : ''}`}
          onClick={() => setTab('loop')}
        >
          节拍 / Loop
        </button>
      </div>

      {tab === 'text' && (
        <div className="panel">
          <label className="field-label">描述你想要的歌曲</label>
          <textarea
            className="desc"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="例如：深夜雨声里想念旧朋友，LoFi 节奏，带钢琴"
          />
          <p className="hint">至少 4 个字，越具体效果越好。</p>
        </div>
      )}

      {tab === 'voice' && (
        <div className="panel">
          <label className="field-label">录制一段你的声音（3–30 秒）</label>
          {recording ? (
            <button className="btn danger" onClick={stopRecording}>
              ■ 停止（已录 {recSeconds}s）
            </button>
          ) : (
            <button className="btn primary" onClick={startRecording}>
              ● 开始录音
            </button>
          )}
          {recFile && (
            <span className="rec-file">
              已就绪 · {Math.round(recFile.size / 1024)} KB
            </span>
          )}
          {recFile && (
            <audio
              controls
              src={URL.createObjectURL(recFile)}
              style={{ width: '100%' }}
            />
          )}
        </div>
      )}

      {tab === 'loop' && (
        <div className="panel">
          <label className="field-label">上传一段节拍 / loop</label>
          <input
            ref={loopInputRef}
            type="file"
            accept="audio/*"
            className="file"
            onChange={onLoopFile}
          />
          {loopFile && (
            <>
              <div className="rec-file">已选择：{loopFile.name}</div>
              <audio
                controls
                src={URL.createObjectURL(loopFile)}
                style={{ width: '100%' }}
              />
            </>
          )}
        </div>
      )}

      <details className="advanced" open={showAdvanced}>
        <summary onClick={() => setShowAdvanced((v) => !v)}>
          高级选项（折叠）
        </summary>
        <div className="adv-grid">
          <div>
            <label className="field-label">时长（秒）</label>
            <input
              type="number"
              min={60}
              max={300}
              value={duration}
              onChange={(e) => setDuration(Number(e.target.value))}
              style={{ width: 110 }}
            />
          </div>
          <div>
            <label className="field-label">歌词语言</label>
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              {LANGS.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.label}
                </option>
              ))}
            </select>
          </div>
          <div className="adv-styles">
            <label className="field-label">风格标签（可选）</label>
            <div className="chips">
              {STYLE_PRESETS.map((s) => (
                <button
                  key={s}
                  className={`chip ${styleHints.includes(s) ? 'on' : ''}`}
                  onClick={() => toggleStyle(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
          <div className="adv-lyrics">
            <label className="field-label">自定义歌词（留空由 AI 生成）</label>
            <textarea
              rows={3}
              value={customLyrics}
              placeholder="每行一句，可用 [verse] [chorus] 标注段落"
              onChange={(e) => setCustomLyrics(e.target.value)}
            />
          </div>
        </div>
      </details>

      {err && <div className="error">{err}</div>}

      <button
        className="btn primary big"
        onClick={submit}
        disabled={submitting}
      >
        {submitting ? '提交中…' : '生成歌曲'}
      </button>
    </section>
  )
}

function pickAudioMime() {
  const cands = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg',
    'audio/mp4',
  ]
  for (const c of cands) {
    if (
      typeof MediaRecorder !== 'undefined' &&
      MediaRecorder.isTypeSupported(c)
    )
      return c
  }
  return ''
}