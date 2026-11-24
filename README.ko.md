# SongForge — 생각 하나, 방송 가능한 완성곡 하나

> 생각 하나를 주세요 (한 문장 / 당신의 목소리 / 비트 루프), 몇 분 안에
> **믹싱·마스터링 완료, 클리핑 없음, 볼륨 표준 충족(-14 LUFS / -1 dBTP)**
> 완성곡을 받으세요: `master.wav` + `master.mp3` + 5개 분트랙(drums / bass / chords / melody / vox)
> + 구간별 가사. 스트리밍 플랫폼에 바로 업로드할 수 있습니다.

<div align="center">

[English](./README.en.md#top) · [简体中文](./README.zh.md#top) · [日本語](./README.ja.md#top) · [한국어](./README.ko.md#top)

</div>

---

## 1. 이것이 무엇인가 / 핵심 구상

SongForge는 **유료 상용을 전제로 한 프로덕션 표준**(데모가 아닙니다)으로 설계·구현된
AI 음악 생성기(백엔드 + 프론트엔드)입니다. 핵심 구상은 5가지:

1. **제로 진입장벽**: 첫 화면은 입력 박스 1개 + 업로드 버튼 2개뿐. BPM·조성·압축비 같은
   전문용어는 일절 등장하지 않습니다. 고급 옵션은 접기(폴더블) 패널에 넣어 기본적으로 닫혀 있습니다.
2. **빠른 피드백**: 진행 바를 가만히 기다리게 하지 않습니다. 파이프라인이
   **먼서 후렴 훅(30초)을 렌더링해 즉시 push**(목표: 25초 안에 듣게).
   전체 곡은 백그라운드에서 생성을 계속합니다.
3. **완성품 레벨**: 출력은 "AI 오디오 조각"이 아닙니다.
   intro / verse / chorus / bridge / outro 구조를 갖추고, 스트리밍 표준에 캘리브레이션
   (기본 **-14 LUFS / -1 dBTP**), 클리핑 없음, 절단 없음.
   WAV + MP3 + 분트랙 + 가사를 포함해 그대로 발행 가능합니다.
4. **플러그인 가능한 음악 엔진**: 기본은 **결정론적 DSP 합성 엔진**
   (SimulatedProvider)로 외부 의존성 제로, 결과 재현 가능, 비용 관리 가능.
   실모델(Suno / OpenAudio 계열 REST 게이트웨이)로 전환은 환경변수 한 줄, 코드 수정 제로.
5. **프로덕션 3종 세트**: **관측 가능성**(구조화 JSON 로그 + Prometheus + 선택적 OTel 트레이스),
   **재시도**(스테이지별 유계 리트라이 + 지수 백오프), **비용 관리**
   (스테이지별 과금 + 작업별 예산 + 24h 일일 상한, 초과 시 즉시 중단·추가 지출 없음).

### MVP에서 명시적으로 하지 않는 것 ("도움이 되고자" 함부로 구현하지 마세요)

DAW급 멀티트랙 에디터, MIDI 내보내기/편집, 다중 사용자 협업,
Spotify 자동 배포, 네이티브 모바일 앱, 모델 자체 학습.

## 2. 세 가지 제품 약속

| 약속 | 구현 방식 | 검증 |
|---|---|---|
| 제로 진입장벽 | 첫 화면은 입력 1개 + 업로드 2개; 전문용어는 접기 패널에 | :5173 열기 |
| 빠른 피드백(≤25초 훅) | `preview` 스테이지(후렴 전용 합성 + 라이트 마스터)가 `preview_ready` 발행 | `smoke_test.py` SLO 항목; `/metrics`의 `sf_preview_latency_seconds` |
| 완성품 레벨 | 구조화 구간 합성 → 믹싱 → 2단 마스터 → MP3/분트랙/가사 | `smoke_test.py` 12/12 |

## 3. 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│ 프론트엔드 (React + Vite)                                        │
│   입력 페이지: ①한 문장 ②녹음 ③루프 업로드                       │
│   결과 페이지: 진행 + 미리듣기 + 다운로드 + 가사                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │  REST /v1  +  WebSocket /ws/jobs/{id}
┌──────────────────────────────┴──────────────────────────────────┐
│  FastAPI 애플리케이션                                             │
│   라우트: 생성 / 업로드 / 목록 / 상세 / 취소 / 다운로드 /          │
│           헬스 / 메트릭                                           │
│   Worker(내장 스레드 or 독립 프로세스): 원자적 작업 접수 → 오케스트레이터 │
│   EventBus: stage / preview_ready / succeeded / failed 이벤트     │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│  파이프라인 오케스트레이터 (스테이지별: 재시도 + 비용 게이트 + 이벤트)   │
│   intent → lyrics → preview(≤25s SLO) → compose → mix_master    │
│            → export_mp3                                          │
│                                                                  │
│   음악 Provider (플러그인 가능):                                   │
│     SimulatedProvider   내장 결정론적 합성 (기본·제로 의존)          │
│     HttpMusicProvider   실모델 REST 게이트웨이 (SF_PROVIDER=http)   │
│                                                                  │
│   DSP 엔진: 믹싱 / 마스터(R128 LUFS + BS.1770 트루피크,            │
│             ffmpeg 캘리브레이션) / 비트 분석 /                      │
│             보이스프린트 / MP3 내보내기                             │
│   영속화: SQLAlchemy (Job / StageAttempt / CostLedger)            │
└─────────────────────────────────────────────────────────────────┘
```

**핵심 설계 설명(입문자용):**

- **2단 마스터링**: 1차는 프로세스 내 완결(컴프레션 → 하이패스 → 정규화 → 리미팅;
  외부 도구 불필요, 어디서든 테스트 가능). 2차에서 ffmpeg의 `ebur128` 필터
  (스트리밍이 쓰는 동일한 표준)로 **실측** 후 목표값까지 정확히 이득 보정.
  ffmpeg가 없는 환경은 1차로 자동 다운그레이드.
- **원자적 작업 접수**: worker는 조건부 UPDATE(`WHERE status='queued'`)로
  작업을 경쟁 접수하므로, 여러 컨테이너라도 동일 작업의 중복 처리는 일어나지 않음.
- **비용 게이트는 지출 전에 확인**: 예산을 확인한 뒤 과금 기록.
  게이트에 막힌 작업은 더 이상 돈을 쓰지 않음.

## 4. 설치 가이드

### 4.1 필요한 것 (모두 무료)

| 도구 | 용도 | 설치 |
|---|---|---|
| **Python 3.10+** | 백엔드 | python.org ("Add to PATH" 체크) |
| **ffmpeg** | MP3 인코딩 + 볼륨 캘리브레이션 | `winget install Gyan.FFmpeg` (Windows) |
| **Node.js 18+** | 프론트엔드 | nodejs.org (LTS) |

PowerShell에서 확인(버전이 출력되면 OK):

```powershell
python --version
ffmpeg -version
node --version
```

> ffmpeg 없이도 동작합니다(MP3 출력과 볼륨 캘리브레이션만 다운그레이드).
> 다만 "완성품 레벨" 약속을 검증하려면 설치를 권장합니다.

### 4.2 코드 확보

```powershell
git clone <저장소 주소> songforge
cd songforge
```

### 4.3 백엔드 의존성

```powershell
pip install -r requirements.txt -r requirements-dev.txt
```

### 4.4 프론트엔드 의존성

```powershell
cd frontend
npm install
```

> 첫 `npm install`은 수 분 걸릴 수 있습니다(React/Vite 패키지 다운로드).
> 느린 네트워크: `npm config set registry https://registry.npmmirror.com`

### 4.5 (선택) 환경변수

기본 설정으로 끝-to-끝 바로 동작(내장 합성 엔진, 외부 의존 제로).
**이 단계는 완전히 건너뛸 수 있습니다.** 실모델 연결이나 볼륨 목표 변경 시에만:

```powershell
copy .env.example .env
```

설정 항목 전체는 §9 참고.

## 5. 실행

**터미널 두 개를** 여세요.

터미널 1 — 백엔드(API + 내장 worker):

```powershell
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

아래 3줄이 보이면 성공:

```
songforge starting v0.1.0 provider=simulated worker=embedded
worker starting (poll=0.5s)
Uvicorn running on http://127.0.0.1:8000
```

터미널 2 — 프론트엔드:

```powershell
cd frontend
npm run dev
```

**브라우저에서 http://localhost:5173을 여세요.**
"생각 하나, 바로 발행 가능한 곡" + 3개 진입 카드가 보이면 실행 성공.

> Vite 개발 서버가 `/v1`과 `/ws`를 8000 포트로 프록시하므로
> CORS 설정이나 프론트엔드 코드 수정이 필요 없습니다.

### Docker 방식(프로덕션 토폴로지)

```powershell
docker compose up --build
```

API는 :8000, 내장 worker는 동일 컨테이너. ffmpeg는 이미지 내장;
SQLite와 스토리지는 `sf-data` 볼륨. worker 수평 확장:
같은 이미지의 두 번째 컨테이너를 `SF_ENABLE_WORKER=true`로 띄우고,
API 컨테이너는 `SF_ENABLE_WORKER=false` — 원자적 접수로 중복 처리 방지.

## 6. 사용

### 6.1 웹 UI

첫 화면에서 진입점을 선택:

1. **한 문장**: "비 내리는 밤에 옛 친구를 그리워하는 LoFi 소조, 후렴은 캐치하게" 정도면 충분.
2. **자기 목소리 녹음**: 3–30초(브라우저가 마이크 권한을 요구). 음색 특징으로 보컬을 형성.
3. **비트 / 루프 업로드**: BPM / 조성 / 에너지를 분석해 생성 곡과 정렬.

제출 후 결과 페이지에서 **먼저 30초 훅 미리듣기**(즉시 청취 가능), 전체 곡 완료 후
마스터 플레이어 + 다운로드 영역(master.wav / master.mp3 / 5개 분트랙 / 가사.txt).

### 6.2 API (curl / 스크립트)

```powershell
# 1) 텍스트 입력
$body = '{"description":"lofi rain night","input_type":"text","duration_secs":90,"language":"en"}'
$r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/jobs" -Method Post -Body $body -ContentType "application/json"
$jobId = $r.job_id

# 2) 상태 폴링 (또는 WebSocket /ws/jobs/$jobId)
while ($true) {
  $j = Invoke-RestMethod "http://127.0.0.1:8000/v1/jobs/$jobId"
  if ($j.status -in "succeeded","failed","cancelled") { break }
  Start-Sleep 2
}

# 3) 다운로드
Invoke-WebRequest "http://127.0.0.1:8000/v1/jobs/$jobId/files/master.mp3" -OutFile master.mp3
```

보이스 / 루프 진입(**메타데이터는 multipart 폼 필드이며, URL 쿼리 파라미터가 아님**):

```powershell
curl.exe -F "file=@C:\path\to\clip.wav" -F "description=gentle lofi" -F "duration_secs=90" -F "language=en" http://127.0.0.1:8000/v1/jobs/voice
```

전체 엔드포인트:

| 메소드 | 경로 | 설명 |
|---|---|---|
| POST | `/v1/jobs` | 텍스트 입력 |
| POST | `/v1/jobs/voice` · `/v1/jobs/loop` | multipart 업로드(file + 폼 필드) |
| GET | `/v1/jobs` · `/v1/jobs/{id}` | 목록 / 상세(자산·가사·비용) |
| POST | `/v1/jobs/{id}/cancel` | 대기 중 작업 취소 |
| GET | `/v1/jobs/{id}/files/{name}` | 다운로드 |
| WS | `/ws/jobs/{id}` | 진행 스트림 |
| GET | `/healthz` · `/metrics` | 헬스 / Prometheus |

> `SF_API_KEY` 설정 시 `/v1` 전체가 `X-API-Key` 헤더를 요구
> (WebSocket는 브라우저가 WS 핸드셰이크에서 커스텀 헤더를 못 두므로 `?api_key=` 쿼리 허용).
> 유료 공개 전 **반드시** 설정하세요.

## 7. 테스트와 검증

```powershell
# 41개 유닛 + 통합 테스트
python -m pytest backend/tests -q

# E2E 스모크: 실제 작업 하나, 제품 약속을 조항별 검증 (ffmpeg 필요)
python backend/smoke_test.py
# 기대: 12/12 checks passed
```

12개 항목: 작업 성공, master.wav 존재, 정확 시간(절단 없음),
**ffmpeg 실측 LUFS ≈ -14**, **트루피크 ≤ -1 dBTP(클리핑 없음)**, 유효 MP3,
5개 분트랙, 가사 구간 구조, 훅 미리듣기 SLO, `preview_ready` 이벤트,
비용 내역, 예산 준수.

## 8. 함정 가이드 (실제로 빠진 함정, 빈도순)

1. **포트 5173을 남은 node 프로세스가 점유**(가장 빈발)
   증상: `npm run dev`가 출력 없이 멈추거나 브라우저 5173 연결 거부.
   Vite가 **무언 중 5174로** 옮겨 "동작 안 하는" 것처럼 보임.
   해결:
   ```powershell
   Get-Process -Name node | Stop-Process -Force
   Get-NetTCPConnection -LocalPort 5173 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
   ```
   이 저장소의 `vite.config.js`는 `strictPort: true`로 포트 점유 시
   **오류로 즉시 종료**(무언 포트 전환 안 함).

2. **Vite가 IPv6 `::1`에만 바인딩**: `127.0.0.1:5173` 불통.
   이미 수정(`host: true`). 삭제하지 마세요.

3. **업로드 메타데이터 유실(프론트/백엔드 계약)**: 브라우저 `FormData`는 필드를
   multipart **폼 필드**로 전송; 백엔드가 `Query`로 선언하면 값을 묵살해서 못 받음.
   이 저장소는 `Form`으로 통일하고 `test_frontend_contract.py`로 잠금.
   **이 두 엔드포인트를 Query로 되돌리지 마세요.**

4. **PowerShell이 pip/npm의 stderr를 오류로 취급**: stderr로 upgrade notice가 나가
   "exited with code 1"을 보아도 실제로는 성공. 실제 출력·임포트 가능 여부로 판단.

5. **ffmpeg ebur128 필드명**: 트루피크는 **`TPK:`**/`FTPK:`,
   `TP:`가 아님; 통합 볼륨은 요약 줄 `I: -xx LUFS`.
   정규식 오류 시 `-inf` 바닥값을 읽어 "클리핑 없음"이 거짓 통과가 됨.

6. **샘플별 Python 루프 DSP는 실용 불가**: 90초 × 44100 Hz ≈ 400만 샘플.
   전부 벡터화(numpy 슬라이딩 윈도우, scipy SOS) — 전체 파이프라인 simulated 모드
   90초 완성 곡 5초 미만.

7. **BS.1770 K-웨이트 셸프 계수는 44.1kHz에서 직접형이 수치 불안정**
   (극이 단위 원 밖 → NaN). 그래서 이 프로젝트는
   **프로세스 안에서 셸프를 수작업하지 않고** "단순화 K-웨이트 1단 + ffmpeg
   ebur128 권위 캘리브레이션" 2단 방식을 채택. "최적화"해 프로세스 안에서 전량 R128로
   만들지 마세요(NaN이 섞임).

8. **`Session.get()`에 클래스 인자 누락**: `s.get(job_id)`는
   `missing positional argument 'ident'`로 폭발; `s.get(Job, job_id)`가 정확.

9. **키가 저장소에 들어가지 않게**: `.env`·`.data/`·`*.db`는 gitignore 완료.
   `SF_API_KEY`, GitHub 토큰, 모델 API 키는 **절대** 커밋하지 마세요.

10. **취소 실행 중 작업 즉시 중단 안 함**: 취소는 대기 중 작업만 차단;
    실행 중 작업은 현재 스테이지를 끝까지(설계상, 반쪽 파일 방지).
    완전히 중단하려면 worker 재시작.

## 9. 구성 참조

모든 설정은 환경변수 / `.env`(`SF_` 접두, pydantic-settings 매핑):

| 변수 | 기본값 | 의미 |
|---|---|---|
| `SF_PROVIDER` | `simulated` | 실모델은 `http` |
| `SF_PROVIDER_BASE_URL` / `SF_PROVIDER_API_KEY` | — | 실모델 REST 게이트웨이 |
| `SF_LLM_BASE_URL` / `SF_LLM_API_KEY` / `SF_LLM_MODEL` | — | 선택 LLM(의도/가사); 미설정 시 결정론적 폴백 |
| `SF_TARGET_LUFS` / `SF_TRUE_PEAK_DB` | -14.0 / -1.0 | 스트리밍 볼륨 목표 |
| `SF_PREVIEW_SECONDS` / `SF_PREVIEW_SLO_SECONDS` | 30 / 25 | 훅 길이 / SLO |
| `SF_JOB_BUDGET_USD` / `SF_DAILY_COST_CAP_USD` | 2.00 / 500.0 | 비용 게이트 |
| `SF_PRICING_*_USD` | 0.02 / 0.15 / 0.01 | 스테이지별 과금 |
| `SF_MAX_STAGE_ATTEMPTS` / `SF_STAGE_RETRY_*` | 3 / 1.0 / 15.0 | 재시도 정책 |
| `SF_API_KEY` | — | 설정 시 `/v1` 전체 인증 요구 |
| `SF_DATABASE_URL` | `sqlite:///.data/songforge.db` | 프로덕션은 PostgreSQL 권장 |
| `SF_STORAGE_DIR` | `.data/storage` | 오디오 산출물 루트 |
| `SF_ENABLE_WORKER` / `SF_WORKER_CONCURRENCY` | true / 2 | 내장 worker / 동시성 |
| `SF_OTEL_ENDPOINT` | — | 선택 OTLP 트레이싱 |

## 10. 자주 묻는 질문 (FAQ)

**Q: 출력 바로 들을 수 있나요?**
네. `master.wav`는 44.1kHz 스테레오 PCM16; `master.mp3`는 320kbps.

**Q: simulated 출력이 "블록 소리"처럼 들리는 건?**
기본 SimulatedProvider는 **공학 검증용 결정론적 신스**
(발진기 + ADSR + 노이즈 드럼). 파이프라인/마스터/내보내기 전체의 정확성과
재현성을 보장하기 위함. "완성품" 음감은 `SF_PROVIDER=http`로 실모델 연결 후에 옵니다.

**Q: 미리듣기와 전체 곡이 두 번 생성되는 이유?**
미리듣기는 후렴만 합성(빠르고 저렴)해 SLO 안에 훅을 맞추고, 전체 곡은 별도 생성으로
풀 퀄리티 보장. 미리듣기 가속이 전체 곡 품질에 영향을 주지 않습니다.

**Q: 스토리지가 계속 자나요?**
네. 작업 행 + 오디오 산출물이 `.data/`에 쌓입니다. 프로덕션은 보존 정책을 계획
(MVP에는 미내장).

**Q: 새로운 입력 타입 추가하는 법?**
라우트 추가(`_create_audio_job` 패턴 재사용) → `Job.input_type` enum 확장 →
오케스트레이터 `_load_context`에 ingest 분기 추가. 파이프라인 나머지는 그대로.

---

<div align="center">

*— 서명: 설계·구축·테스트·서명: Agnes-3.0-flash (Sapiens AI)*

</div>
