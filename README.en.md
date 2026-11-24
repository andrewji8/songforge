# SongForge — One Thought, Back a Radio-Ready Song

<div align="center">

[English](./README.en.md#top) · [简体中文](./README.zh.md#top)

</div>

> Give it a thought (a sentence / your own voice / a beat loop) and in a few minutes
> get a **mixed, mastered, clipping-free, loudness-compliant** finished song:
> `master.wav` + `master.mp3` + 5 stems (drums / bass / chords / melody / vox)
> + section-tagged lyrics. Ready to upload to streaming platforms.

> **中文详细版见 [README.md](README.md)**

---

## 1. What This Is & the Core Ideas

SongForge is a complete **AI song generator** (backend + frontend) built to
**production standard for a paid product**, not a demo. Five core ideas:

1. **Zero barrier**: the first screen is one input box + two upload buttons.
   No jargon (BPM / key / compression ratio) — advanced options live in a
   collapsed panel, off by default.
2. **Fast feedback**: instead of a waiting bar, the pipeline **renders the
   chorus hook (30 s) first and pushes it immediately** (target: audible in
   ≤ 25 s) while the full song keeps generating in the background.
3. **Finished output, not a snippet**: intro/verse/chorus/bridge/outro
   structure, calibrated to streaming standards (default **-14 LUFS /
   -1 dBTP**), no clipping, no truncation, delivered as WAV + MP3 + stems +
   lyrics.
4. **Pluggable music engine**: the default **deterministic DSP synth engine**
   (SimulatedProvider) needs zero external dependencies, is reproducible and
   cost-controlled, so the entire pipeline always runs and tests pass.
   Swapping in a real model (a Suno / OpenAudio-style REST gateway) is an
   env-var change — no code edits.
5. **Production trio**: **observability** (structured JSON logs + Prometheus
   metrics + optional OTel traces), **retries** (bounded retry per pipeline
   stage with exponential backoff), **cost control** (per-stage billing into a
   cost ledger + per-job budget + 24 h daily cap; exceeding it aborts and
   burns no further money).

### Explicitly NOT in the MVP (please don't "helpfully" add these)

DAW-level multi-track editor, MIDI export/editing, multi-user collaboration,
auto-distribution to Spotify, native mobile app, self-training models.

## 2. The Three Product Promises

| Promise | How | Verified by |
|---|---|---|
| Zero barrier | One input box + two upload buttons; jargon hidden in a foldable panel | open :5173 |
| Fast feedback (≤ 25 s hook) | `preview` stage (chorus-only synth + light master) emits `preview_ready` | `smoke_test.py` SLO check; `sf_preview_latency_seconds` on `/metrics` |
| Finished output | structured-section synthesis → mix → two-pass master (in-process + ffmpeg `ebur128` calibration) → MP3 / stems / lyrics | `smoke_test.py`, 12/12 checks |

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Frontend (React + Vite)                                          │
│  entry page: ① text ② record voice ③ upload loop                │
│  result page: progress + preview player + downloads + lyrics     │
└──────────────────────────────┬──────────────────────────────────┘
                               │  REST /v1  +  WebSocket /ws/jobs/{id}
┌──────────────────────────────┴──────────────────────────────────┐
│  FastAPI application                                             │
│  routes: create / upload / list / detail / cancel / download /    │
│          health / metrics                                        │
│  worker (embedded thread or standalone process):                 │
│    atomic job claim → orchestrator                               │
│  EventBus: stage / preview_ready / succeeded / failed events     │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│  Pipeline orchestrator (per stage: retry + cost gate + events)   │
│   intent → lyrics → preview (≤ 25 s SLO) → compose → mix_master │
│            → export_mp3                                          │
│                                                                  │
│  Pluggable music provider:                                       │
│    SimulatedProvider   built-in deterministic synth (default)    │
│    HttpMusicProvider   real-model REST gateway (SF_PROVIDER=http)│
│                                                                  │
│  DSP engine: mix / master (R128 LUFS + BS.1770 true peak,       │
│              ffmpeg-calibrated) / beat analysis / voiceprint /   │
│              MP3 export                                          │
│  Persistence: SQLAlchemy (Job / StageAttempt / CostLedger)       │
└─────────────────────────────────────────────────────────────────┘
```

**Key design notes (explained simply):**

- **Two-pass mastering**: pass 1 runs entirely in-process (compress → high-pass
  → normalize → limit; no external tools, testable anywhere). Pass 2 measures
  the rendered WAV with ffmpeg's `ebur128` filter — the same standard streaming
  platforms gate on — and nudges exact makeup gain to the target. Without
  ffmpeg it gracefully degrades to pass 1 only.
- **Atomic job claiming**: workers claim jobs via a conditional
  `UPDATE … WHERE status='queued'`, so multiple worker containers never
  double-process a job.
- **Cost gate checks *before* spending**: budgets are verified before the
  billed amount is recorded; a gated job spends nothing further.

## 4. Installation (step by step, beginner-friendly)

### 4.1 Prerequisites (all free)

| Tool | Why | Install |
|---|---|---|
| **Python 3.10+** | backend | python.org — check **“Add to PATH”** |
| **ffmpeg** | MP3 encoding + loudness calibration | `winget install Gyan.FFmpeg` on Windows, or your package manager |
| **Node.js 18+** | frontend | nodejs.org (LTS) |

Verify (each should print a version):

```powershell
python --version
ffmpeg -version
node --version
```

> The app runs **without** ffmpeg (MP3 export & loudness calibration just
> degrade); but to verify the "finished-output" promise, install it.

### 4.2 Get the code

```powershell
git clone <your-repo> songforge
cd songforge
```

### 4.3 Backend dependencies

```powershell
pip install -r requirements.txt -r requirements-dev.txt
```

### 4.4 Frontend dependencies

```powershell
cd frontend
npm install
```

> The first `npm install` can take a few minutes (React/Vite packages) —
> that's normal, not a hang. On slow networks: `npm config set registry https://registry.npmmirror.com`.

### 4.5 (Optional) Configuration

Defaults work end-to-end out of the box (built-in synth engine, zero
external deps) — **you can skip this entirely**. Create a `.env` only when
wiring a real model or changing loudness targets:

```powershell
copy .env.example .env
```

All configuration items are in §9.

## 5. Running

**Two terminals.**

Terminal 1 — backend (API + embedded worker):

```powershell
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

You're good when you see:

```
songforge starting v0.1.0 provider=simulated worker=embedded
worker starting (poll=0.5s)
Uvicorn running on http://127.0.0.1:8000
```

Terminal 2 — frontend:

```powershell
cd frontend
npm run dev
```

**Open http://localhost:5173.**

> Vite dev-proxy already forwards `/v1` and `/ws` to :8000, so no CORS
> configuration is needed.

### Docker (production topology)

```powershell
docker compose up --build
```

API on :8000 with the embedded worker; ffmpeg baked into the image;
SQLite + storage on the `sf-data` volume. To scale workers: run a second
container with `SF_ENABLE_WORKER=true` and set `SF_ENABLE_WORKER=false` on
the API container — atomic claiming guarantees no double-processing.

## 6. Usage

### 6.1 Web UI

Pick one entry point on the first screen:

1. **One sentence**: "a late-night lo-fi minor tune about missing an old
   friend in the rain, catchy chorus" — no jargon needed.
2. **Record your voice**: 3–30 s (browser will ask mic permission).
   Timbre features shape the vocal stem; melody aligns to your pitch range.
3. **Upload a beat/loop**: the analyzer extracts BPM / key / energy and the
   generated song aligns to it.

After submitting you land on the results page: the **30-second hook preview
plays first**, and when the full song finishes you get the master player +
downloads (master.wav / master.mp3 / 5 stems / lyrics.txt).

### 6.2 API

```powershell
# 1) text-input job
$body = '{"description":"lofi rain night","input_type":"text","duration_secs":90,"language":"zh"}'
$r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/jobs" -Method Post -Body $body -ContentType "application/json"
$jobId = $r.job_id

# 2) poll (or open WebSocket /ws/jobs/$jobId for live events)
while ($true) {
  $j = Invoke-RestMethod "http://127.0.0.1:8000/v1/jobs/$jobId"
  if ($j.status -in "succeeded","failed","cancelled") { break }
  Start-Sleep 2
}

# 3) download
Invoke-WebRequest "http://127.0.0.1:8000/v1/jobs/$jobId/files/master.mp3" -OutFile master.mp3
```

Voice / loop entries (**metadata are multipart FORM fields, not query
params**):

```powershell
curl.exe -F "file=@C:\path\to\clip.wav" -F "description=gentle lofi" -F "duration_secs=90" -F "language=zh" http://127.0.0.1:8000/v1/jobs/voice
```

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/jobs` | text input |
| POST | `/v1/jobs/voice` · `/v1/jobs/loop` | multipart (file + form fields) |
| GET | `/v1/jobs` · `/v1/jobs/{id}` | list / detail (assets, lyrics, cost) |
| POST | `/v1/jobs/{id}/cancel` | cancel queued jobs |
| GET | `/v1/jobs/{id}/files/{name}` | preview.wav / master.wav / master.mp3 / stems/*.wav / lyrics.txt |
| WS | `/ws/jobs/{id}` | progress stream |
| GET | `/healthz` · `/metrics` | liveness · Prometheus |

> With `SF_API_KEY` set, all `/v1` routes require the `X-API-Key` header
> (WebSocket accepts `?api_key=` since browsers can't set custom headers in
> the WS handshake). **Set it before going paid.**

## 7. Tests & Verification

```powershell
# 41 unit + integration tests (DSP / orchestrator / API / regressions)
python -m pytest backend/tests -q

# end-to-end smoke: one real job, checks every product promise (needs ffmpeg)
python backend/smoke_test.py
# expected: 12/12 checks passed
```

The 12 checks cover: job success, master.wav present, exact duration
(no truncation), **ffmpeg-measured LUFS ≈ -14**, **true peak ≤ -1 dBTP
(no clipping)**, valid MP3, 5 stems, lyrics section structure, hook-preview
SLO, `preview_ready` event, cost ledger entry, budget respected.

## 8. Pitfall Guide (pitfalls we actually hit)

Ranked by frequency:

1. **Leftover `node` process holding port 5173** (most common)
   Symptom: `npm run dev` hangs with no output, or the browser refuses
   5173. Vite silently falls back to 5174 → looks "dead".
   Fix:
   ```powershell
   Get-Process -Name node | Stop-Process -Force
   Get-NetTCPConnection -LocalPort 5173 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
   ```
   This repo sets `strictPort: true` in `vite.config.js` so a busy port
   fails loudly instead of silently hopping ports.

2. **Vite binding IPv6 `::1` only**: `127.0.0.1:5173` refused while
   `[::1]:5173` works. Fixed via `host: true` in `vite.config.js` — don't
   remove it.

3. **Upload metadata silently lost (frontend/backend contract)**: browsers
   send `FormData` as multipart **form fields**; if backend params are
   declared `Query`, values are silently dropped (description → None,
   duration → default). This repo uses `Form` and locks the contract with
   `test_frontend_contract.py`. **Never "fix" those two endpoints back to
   Query.**

4. **PowerShell treats pip/npm stderr as errors**: an upgrade notice on
   stderr yields "Command exited with code 1" even when the install
   succeeded. Judge by actual output / importability, not exit code.

5. **ffmpeg `ebur128` field names**: true peak is **`TPK:`** (and `FTPK:`),
   not `TP:`; integrated loudness is the summary line `I: -xx LUFS`. Wrong
   regex silently reads `-inf` floors and makes "no clipping" a false
   pass. `measure_lufs_ffmpeg` in `app/dsp/loudness.py` handles it.

6. **Per-sample Python DSP loops are unusably slow**: 90 s × 44 100 Hz =
   ~4 M samples. Everything here is vectorized (numpy sliding-window,
   scipy SOS filters); the full simulated pipeline finishes a 90 s song in
   < 5 s wall-clock.

7. **BS.1770 K-weighting shelf coefficients are numerically unstable at
   44.1 kHz in direct form** (poles leave the unit circle → NaN). That's
   why this project does **not** hand-roll the shelf in-process: the
   two-pass design (simplified in-process K-weighting + ffmpeg `ebur128`
   arbiter) exists for this reason. Don't "optimize" it away.

8. **`Session.get()` needs the class**: `s.get(job_id)` raises
   `missing positional argument 'ident'`; use `s.get(Job, job_id)`.

9. **Keys in the repo**: `.env`, `.data/`, `*.db` are git-ignored.
   **Never commit `SF_API_KEY`, GitHub tokens, or model API keys.**

10. **Cancel doesn't interrupt running jobs**: cancel only blocks queued
    jobs; a running job finishes its current stage (by design, to avoid
    half-written files). Restarting the worker is the hard stop.

## 9. Configuration Reference

All settings via env vars or `.env` (`SF_` prefix, mapped by pydantic-settings):

| Variable | Default | Meaning |
|---|---|---|
| `SF_PROVIDER` | `simulated` | `http` for a real music model |
| `SF_PROVIDER_BASE_URL` / `SF_PROVIDER_API_KEY` | — | real-model REST gateway |
| `SF_LLM_BASE_URL` / `SF_LLM_API_KEY` / `SF_LLM_MODEL` | — | optional LLM gateway (intent/lyrics); deterministic fallbacks without it |
| `SF_TARGET_LUFS` / `SF_TRUE_PEAK_DB` | -14.0 / -1.0 | streaming loudness target |
| `SF_PREVIEW_SECONDS` / `SF_PREVIEW_SLO_SECONDS` | 30 / 25 | hook length / SLO |
| `SF_JOB_BUDGET_USD` / `SF_DAILY_COST_CAP_USD` | 2.00 / 500.0 | cost gates |
| `SF_PRICING_PREVIEW_USD` / `SF_PRICING_FULL_USD` / `SF_PRICING_MIX_MASTER_USD` | 0.02 / 0.15 / 0.01 | per-stage billing |
| `SF_MAX_STAGE_ATTEMPTS` / `SF_STAGE_RETRY_BASE_DELAY_S` / `SF_STAGE_RETRY_MAX_DELAY_S` | 3 / 1.0 / 15.0 | retry policy |
| `SF_API_KEY` | — | requires `X-API-Key` on `/v1` |
| `SF_DATABASE_URL` | `sqlite:///.data/songforge.db` | use PostgreSQL in prod |
| `SF_STORAGE_DIR` | `.data/storage` | audio artifacts root |
| `SF_ENABLE_WORKER` / `SF_WORKER_CONCURRENCY` | true / 2 | embedded worker / concurrency |
| `SF_OTEL_ENDPOINT` | — | optional OTLP tracing |

## 10. FAQ

**Q: Can I just listen to the output?**
Yes. `master.wav` is 44.1 kHz stereo PCM16; `master.mp3` is 320 kbps.

**Q: The simulated output sounds "blocky" — is that a bug?**
No. The default SimulatedProvider is a deterministic *engineering* synth
(oscillators + ADSR + noise drums) that guarantees the pipeline / mastering /
export chain is correct and reproducible. Real finished-sounding audio
arrives when you wire `SF_PROVIDER=http` to a real model.

**Q: Why two generations (preview + full) instead of just the first 30 s
of the full song?**
The preview stage synthesizes only the chorus (faster, cheaper) so the hook
lands inside the SLO, while the full song is generated independently at
full quality — preview speed never trades off full-song quality.

**Q: Does storage grow forever?**
Yes — job rows and audio artifacts accumulate under `.data/`. Plan a
retention job in production (not built into the MVP).

**Q: How do I add a new input type?**
Add a route (reuse the `_create_audio_job` pattern) → extend the
`Job.input_type` enum → add an ingest branch in the orchestrator's
`_load_context`. The rest of the pipeline is untouched.

---

<div align="center">

*— Signed: built and signed off by Agnes-3.0-flash (Sapiens AI)*

</div>
