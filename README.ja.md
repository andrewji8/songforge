# SongForge — 一言で、ラジオ対応の完成曲が生まれる

> 一つ思い（一文 / 自分の声 / ビートループ）を与えると、数分で
> **ミキシング済み、マスタリング済み、クリップなし、ラウドネス適合**
> の完成曲が手に入ります：`master.wav` + `master.mp3` + 5 チャンネル分け
> （drums / bass / chords / melody / vox）+ セクション付き歌詞。
> ストリーミングプラットフォームに直接アップロード可能です。

<div align="center">

[English](./README.en.md#top) · [简体中文](./README.zh.md#top) · [日本語](./README.ja.md#top) · [한국어](./README.ko.md#top)

</div>

---

## 1. これは何か / 中核コンセプト

SongForge は**有料サービスとして公開する本格的なプロジェクト**（デモではない）として設計・実装された
AI 楽曲ジェネレーター（バックエンド + フロントエンド）です。中核コンセプトは 5 つ：

1. **ゼロ敷居**：ファーストビューは入力ボックス 1 つ + アップロードボタン 2 つのみ。
   BPM・調性・圧縮比のような専門用語は一切出さない。高度なオプションは折りたたみパネル内に収め、
   デフォルトは非表示。
2. **高速フィードバック**：プログレスバーをじっと待たせる代わりに、パイプラインが
   **先にサビのフック（30 秒）をレンダリングし即座に推送**（目標 25 秒以内に聴ける）。
   全曲はバックグラウンドで生成を継続。
3. **完成品レベル**：出力は「AI オーディオ断片」ではありません。
   intro / verse / chorus / bridge / outro 構造を持ち、ストリーミング基準にキャリブレーション
   （デフォルト **-14 LUFS / -1 dBTP**）、クリップなし、カットなし。
   WAV + MP3 + チャンネル分け + 歌詞を付属し、そのまま発行可能。
4. **プラグイン可能な音楽エンジン**：デフォルトは**決定的な DSP 合成エンジン**
   （SimulatedProvider）で、外部依存ゼロ・結果再現可能・コスト制御可能。
   実モデル（Suno / OpenAudio 風の REST ゲートウェイ）への切替は環境変数のみでコード修正ゼロ。
5. **本番三柱**：**可観測性**（構造化 JSON ログ + Prometheus + オプション OTel トレース）、
   **リトライ**（ステージごとの有界リトライ + 指数退避）、**コスト制御**
   （ステージ別課金 + ジョブ別予算 + 24h 日次上限、超過で即中止・更なる支出ゼロ）。

### MVP で明確にやらないこと（「親切」で勝手に実装しないこと）

DAW 級マルチトラックエディタ、MIDI エクスポート / 編集、マルチユーザー協業、
Spotify 自動配信、ネイティブモバイルアプリ、モデル自己学習。

## 2. 三つのプロダクト約束

| 約束 | 実現方法 | 検証 |
|---|---|---|
| ゼロ敷居 | ファーストビューは入力 1 つ + アップロード 2 つ；専門用語は折りたたみパネルに | :5173 を開く |
| 高速フィードバック（≤ 25 秒フック） | `preview` ステージ（サビ専用合成 + ライトムスター）が `preview_ready` を送出 | `smoke_test.py` の SLO 項目；`/metrics` の `sf_preview_latency_seconds` |
| 完成品レベル | 構造化セクション合成 → ミックス → 2 段階マスター → MP3 / 分け / 歌詞 | `smoke_test.py` 12/12 |

## 3. アーキテクチャ

```
┌─────────────────────────────────────────────────────────────────┐
│ フロントエンド (React + Vite)                                     │
│   入力ページ: ①一言 ②録音 ③ループアップロード                         │
│   結果ページ: プログレス + プレビュー + ダウンロード + 歌詞            │
└──────────────────────────────┬──────────────────────────────────┘
                               │  REST /v1  +  WebSocket /ws/jobs/{id}
┌──────────────────────────────┴──────────────────────────────────┐
│  FastAPI アプリ                                                  │
│   ルート: 作成 / アップロード / 一覧 / 詳細 / 中止 / ダウンロード /    │
│           ヘルス / メトリクス                                      │
│   Worker（内蔵スレッド or 独立プロセス）: 原子的ジョブ取得 → 編成器    │
│   EventBus: stage / preview_ready / succeeded / failed イベント    │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│  パイプライン編成器（各ステージ: リトライ + コストゲート + イベント）     │
│   intent → lyrics → preview (≤25s SLO) → compose → mix_master   │
│            → export_mp3                                          │
│                                                                  │
│   音楽 Provider（プラグイン可能）:                                    │
│     SimulatedProvider   内蔵決定的合成（デフォルト・ゼロ依存）          │
│     HttpMusicProvider   実モデル REST ゲートウェイ (SF_PROVIDER=http)  │
│                                                                  │
│   DSP エンジン: ミックス / マスター (R128 LUFS + BS.1770 真ピーク,     │
│                 ffmpeg キャリブレーション) / ビート解析 /            │
│                 声紋特徴 / MP3 エクスポート                           │
│   永続化: SQLAlchemy（Job / StageAttempt / CostLedger）            │
└─────────────────────────────────────────────────────────────────┘
```

**設計要点（入門者向け説明）：**

- **2 段階マスター**：1 回目はプロセス内で完結（圧縮 → 高通 → 正規化 → リミット；
  外部ツール不要・どこでもテスト可能）。2 回目で ffmpeg の `ebur128` フィルタ
  （ストリーミングが使う同一基準）で**実測**し、目標値まで正確にゲイン補正。
  ffmpeg が無い環境は自動的に 1 回目までに降格。
- **原子的ジョブ取得**：worker は条件付き UPDATE（`WHERE status='queued'`）で
  ジョブを奪い合うため、複数コンテナでも同一ジョブの二重処理は起きない。
- **コストゲートは支出前にチェック**：予算を確認してから課金入账。
  ゲートに止められたジョブは更にお金を使わない。

## 4. インストールガイド

### 4.1 必要なもの（すべて無料）

| ツール | 用途 | 導入 |
|---|---|---|
| **Python 3.10+** | バックエンド | python.org（「Add to PATH」にチェック） |
| **ffmpeg** | MP3 符号化 + ラウドネスキャリブレーション | `winget install Gyan.FFmpeg`（Windows） |
| **Node.js 18+** | フロントエンド | nodejs.org（LTS） |

PowerShell で確認（バージョンが出力されれば OK）：

```powershell
python --version
ffmpeg -version
node --version
```

> ffmpeg が無くても動作します（MP3 出力とラウドネスキャリブレーションが降格）。
> ただし「完成品レベル」の約束を検証したいなら導入推奨。

### 4.2 コード取得

```powershell
git clone <あなたのリポジトリアドレス> songforge
cd songforge
```

### 4.3 バックエンド依存

```powershell
pip install -r requirements.txt -r requirements-dev.txt
```

### 4.4 フロントエンド依存

```powershell
cd frontend
npm install
```

> 初回 `npm install` は数分かかることがあります（React/Vite 包のダウンロード）。
> 遅いネットワークでは：`npm config set registry https://registry.npmmirror.com`

### 4.5 （オプション）環境変数

デフォルト設定でそのまま全リンクが通ります（内蔵合成エンジン・外部依存ゼロ）。
**このステップは完全にスキップ可**。実モデル接続やラウドネス目標変更時だけ：

```powershell
copy .env.example .env
```

全設定項目は §9 参照。

## 5. 実行

**2 つのターミナル**を開く。

ターミナル 1 — バックエンド（API + 内蔵 worker）：

```powershell
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

以下の 3 行が現れたら成功：

```
songforge starting v0.1.0 provider=simulated worker=embedded
worker starting (poll=0.5s)
Uvicorn running on http://127.0.0.1:8000
```

ターミナル 2 — フロントエンド：

```powershell
cd frontend
npm run dev
```

**ブラウザで http://localhost:5173 を開く。**
「一言で、直接公開可能な曲を」+ 3 つのエントリカードが現れたら起動成功。

> Vite 開発サーバーは `/v1` と `/ws` を 8000 ポートにプロキシ済み。
> CORS 設定やフロントエンドコード変更は不要。

### Docker 方式（本番トポロジ）

```powershell
docker compose up --build
```

API は :8000、内蔵 worker は同一コンテナ。ffmpeg はイメージ内蔵；
SQLite とストレージは `sf-data` ボリューム。worker 横展開：
同じイメージの 2 番目コンテナを `SF_ENABLE_WORKER=true` で起動、
API コンテナは `SF_ENABLE_WORKER=false` — 原子的取得で二重処理なし。

## 6. 使用

### 6.1 Web UI

ファーストビューでエントリを選択：

1. **一言**：「夜の雨の中で古い友人を思う LoFi の小節、サビはキャッチーに」程度で可。
2. **自分の声録音**：3〜30 秒（ブラウザがマイク権限を要求）。音色特徴でボーカルを塑形。
3. **ビート / ループアップロード**：BPM / 調性 / エネルギーを解析し、生成曲と整列。

提出後、結果ページで**先に 30 秒フックプレビュー**（即聴可）、全曲完成後
マスタープレイヤー + ダウンロード領域（master.wav / master.mp3 / 5 分track / 歌詞.txt）。

### 6.2 API 方式（curl / スクリプト）

```powershell
# 1) テキスト入力
$body = '{"description":"lofi 雨夜","input_type":"text","duration_secs":90,"language":"ja"}'
$r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/jobs" -Method Post -Body $body -ContentType "application/json"
$jobId = $r.job_id

# 2) 状態ポーリング（または WebSocket /ws/jobs/$jobId）
while ($true) {
  $j = Invoke-RestMethod "http://127.0.0.1:8000/v1/jobs/$jobId"
  if ($j.status -in "succeeded","failed","cancelled") { break }
  Start-Sleep 2
}

# 3) ダウンロード
Invoke-WebRequest "http://127.0.0.1:8000/v1/jobs/$jobId/files/master.mp3" -OutFile master.mp3
```

音声 / ループエントリ（**メタデータは multipart フォームフィールド、URL query パラメータではない**）：

```powershell
curl.exe -F "file=@C:\path\to\clip.wav" -F "description=gentle lofi" -F "duration_secs=90" -F "language=ja" http://127.0.0.1:8000/v1/jobs/voice
```

全エンドポイント：

| メソッド | パス | 説明 |
|---|---|---|
| POST | `/v1/jobs` | テキスト入力 |
| POST | `/v1/jobs/voice` · `/v1/jobs/loop` | multipart アップロード（file + フォームフィールド） |
| GET | `/v1/jobs` · `/v1/jobs/{id}` | 一覧 / 詳細（アセット・歌詞・コスト） |
| POST | `/v1/jobs/{id}/cancel` | キュー中ジョブのキャンセル |
| GET | `/v1/jobs/{id}/files/{name}` | ダウンロード |
| WS | `/ws/jobs/{id}` | プログレスストリーム |
| GET | `/healthz` · `/metrics` | ヘルス / Prometheus |

> `SF_API_KEY` 設定後、`/v1` 全ルートが `X-API-Key` ヘッダ必須
> （WebSocket は `?api_key=` クエリパラメータ可、ブラウザ WS ハンドシェイクでは
> カスタムヘッダ不可ため）。有料公開前に**必ず**設定。

## 7. テストと検証

```powershell
# 41 単体 + 統合テスト
python -m pytest backend/tests -q

# E2E スモーク：実ジョブ 1 本、プロダクト約束を逐条検証（ffmpeg 要）
python backend/smoke_test.py
# 期待: 12/12 checks passed
```

## 8. 落とし穴ガイド（実際に踏んだ穴）

発生頻度順：

1. **ポート 5173 を残留 node プロセスが占有**（最頻発）
   症状：`npm run dev` が無出力、またはブラウザ 5173 拒否。Vite は
   **黙って 5174 に切替** → 「起動しない」に見える。
   対処：
   ```powershell
   Get-Process -Name node | Stop-Process -Force
   ```
   本リポジトリの `vite.config.js` は `strictPort: true` でポート占有時に
   **エラーで即終了**（黙って切替しない）。

2. **Vite が IPv6 `::1` だけにバインド**：`127.0.0.1:5173` 不通。
   対処済み（`host: true`）。削除しないこと。

3. **アップロードメタデータ喪失（フロント/バックエンド契約）**：
   ブラウザ `FormData` はフィールドを multipart **フォームフィールド**で送信；
   バックエンドが `Query` 宣言だと黙って取りこぼす。
   本リポジトリは `Form` 統一 + `test_frontend_contract.py` でロック。
   **この 2 エンドポイントを Query に戻さないこと。**

4. **PowerShell が pip/npm の stderr をエラー扱い**：
   upgrade notice が stderr に出て「Command exited with code 1」でも
   実はいつもある。実出力とインポート可否で判定。

5. **ffmpeg ebur128 のフィールド名**：真ピークは **`TPK:`** / `FTPK:`、
   `TP:` ではない；統合ラウドネスはサマリー行 `I: -xx LUFS`。
   正規表現誤りは `-inf` 床値を取り、誤って「クリップなし」を判定する。

6. **サンプル毎 Python ループの DSP は実用不可**：
   90s × 44100 Hz = 400 万サンプル。全て向量化（numpy / scipy SOS）。
   本リポジトリ DSP 全件向量化 → 全パイプライン < 5 秒で 90s 完成曲。

7. **BS.1770 K-ウェイト shelf の直接型係数は 44.1kHz で数値不安定**
   （極点が単位円外 → NaN）。そのため本プロジェクトは
   **プロセス内で shelf を手造りせず**「簡略化 K-ウェイト初回 + ffmpeg
   ebur128 権威キャリブレーション」の 2 段方式を採用。「最適化」で
   プロセス内全量 R128 にしないこと（NaN が混入）。

8. **`Session.get()` でクラス引数漏れ**：`s.get(job_id)` は
   `missing positional argument 'ident'` で爆発；`s.get(Job, job_id)` が正しい。

9. **トークン / シークレットがリポジトリに入らない**：
   `.env`・`.data/`・`*.db` は .gitignore 済み。
   `SF_API_KEY`、GitHub token、モデル API key を**絶対**コミットしない。

10. **キャンセルは実行中ジョブを即停止しない**：キャンセルはキュー中のみブロック；
    実行中は現ステージ完了まで進む（設計上、半端ファイル防止）。
    完全中断は worker リスタート。

## 9. 設定参照

全設定は環境変数 / `.env`（`SF_` プレフィックス、pydantic-settings）：

| 変数 | デフォルト | 説明 |
|---|---|---|
| `SF_PROVIDER` | `simulated` | `http` で実モデル |
| `SF_PROVIDER_BASE_URL` / `SF_PROVIDER_API_KEY` | — | 実モデル REST ゲートウェイ |
| `SF_LLM_BASE_URL` / `SF_LLM_API_KEY` / `SF_LLM_MODEL` | — | オプション LLM（意図/歌詞）；未設定は決定的フォールバック |
| `SF_TARGET_LUFS` / `SF_TRUE_PEAK_DB` | -14.0 / -1.0 | ストリーミングラウドネス目標 |
| `SF_PREVIEW_SECONDS` / `SF_PREVIEW_SLO_SECONDS` | 30 / 25 | フック長 / SLO |
| `SF_JOB_BUDGET_USD` / `SF_DAILY_COST_CAP_USD` | 2.00 / 500.0 | コストゲート |
| `SF_PRICING_*` | 0.02 / 0.15 / 0.01 | ステージ別課金 |
| `SF_MAX_STAGE_ATTEMPTS` / `SF_STAGE_RETRY_*` | 3 / 1.0 / 15.0 | リトライ戦略 |
| `SF_API_KEY` | — | 設定すると `/v1` 全要鑑権 |
| `SF_DATABASE_URL` | `sqlite:///.data/songforge.db` | 本番は PostgreSQL 推奨 |
| `SF_STORAGE_DIR` | `.data/storage` | 音声成果物ルート |
| `SF_ENABLE_WORKER` / `SF_WORKER_CONCURRENCY` | true / 2 | 内蔵 worker / 並列数 |
| `SF_OTEL_ENDPOINT` | — | オプション OTLP トレース |

## 10. FAQ

**Q: 出力はそのまま聴ける？**
はい。`master.wav` は 44.1kHz ステレオ PCM16；`master.mp3` は 320kbps。

**Q: simulated 出力が「ブロック状」に聴こえるのは？**
デフォルト SimulatedProvider は**エンジニアリング検証用の決定的シンセ**
（オシレータ + ADSR + ノイズドラム）。パイプライン / マスター / エクスポート
全リンクの正確性と再現性を保証するため。聴覚の「完成品」質感は
`SF_PROVIDER=http` で実モデル接続時になります。

**Q: プレビューと全曲が 2 回生成するのは？**
プレビューはサビのみ合成（高速・低コスト）で SLO 内にフックを収め、
全曲は別生成でフル品質を保証。プレビュー高速化が全曲品質に影響しない。

**Q: ストレージは無制限に増える？**
はい。ジョブ行 + 音声成果物が `.data/` に蓄積。本番は保持ポリシーを計画
（MVP には未実装）。

**Q: 新しい入力タイプ追加方法？**
ルート追加（`_create_audio_job` パターン流用）→ `Job.input_type` enum 拡張 →
編成器 `_load_context` に ingest 分岐追加。パイプライン他は不変。

---

<div align="center">

*— 署名：設計・構築・テスト・署名：Agnes-3.0-flash（Sapiens AI）*

</div>
