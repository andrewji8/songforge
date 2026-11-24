/**
 * SongForge 前端共享 TypeScript 类型。
 *
 * 与后端 /v1 JSON 契约及 WebSocket 进度帧保持一致，供 api.ts 与
 * 组件层复用，避免 any 泛滥。
 */

export type JobInputType = 'text' | 'voice' | 'loop'

export type JobStatus =
  | 'queued'
  | 'running'
  | 'preview_ready'
  | 'succeeded'
  | 'failed'
  | 'cancelled'

export interface Asset {
  filename: string
  label: string
  url: string
  size_bytes: number
}

export interface LyricSection {
  section: string
  lines: string[]
}

export interface JobDetail {
  job_id: string
  input_type: JobInputType
  status: JobStatus
  key?: string | null
  bpm?: number | null
  duration_secs?: number | null
  cost_usd?: number | null
  error?: string | null
  master_wav_path?: string | null
  master_mp3_path?: string | null
  stems_dir?: string | null
  lyrics_path?: string | null
  preview_wav_path?: string | null
  assets: Asset[]
  lyrics_sections: LyricSection[]
  created_at?: string
  updated_at?: string
  finished_at?: string
}

export interface CreateJobResponse {
  job_id: string
  status: string
}

export interface ProgressEvent {
  job_id: string
  type: string
  stage?: string | null
  detail?: string | null
  error?: string | null
  preview_url?: string | null
  assets?: Asset[]
}

export interface JobMeta {
  description: string
  durationSecs: number
  language: string
  styleHints: string[]
  lyrics?: string | null
}

export interface CreateTextJobInput extends JobMeta {
  kind: 'text'
}

export interface CreateVoiceJobInput extends JobMeta {
  kind: 'voice'
  file: File
}

export interface CreateLoopJobInput extends JobMeta {
  kind: 'loop'
  file: File
}

export type CreateJobInput = CreateTextJobInput | CreateVoiceJobInput | CreateLoopJobInput

export interface ApiError extends Error {
  status?: number
}

export interface SubscribeHandle {
  close: () => void
  closed: () => boolean
}