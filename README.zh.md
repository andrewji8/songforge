# SongForge — 一句话，还你一首能直接发布的歌

<div align="center">

[English](./README.en.md#top) · [简体中文](./README.zh.md#top) · [日本語](./README.ja.md#top) · [한국어](./README.ko.md#top)

</div>

> 给一个念头（一段话 / 一段你自己的声音 / 一段节拍 loop），
> 几分钟后拿到**已混音、已母带、无爆音、响度达标**的成品歌曲：
> `master.wav` + `master.mp3` + 5 组分轨（drums / bass / chords / melody / vox）+ 带段落标注的歌词。
> 可以直接上传流媒体平台。

---

## 目录

1. [这是什么 / 主要构思](#1-这是什么--主要构思)
2. [三条产品承诺（以及它们是怎么做到的）](#2-三条产品承诺)
3. [整体架构](#3-整体架构)
4. [安装指南（小白一步步来）](#4-安装指南)
5. [运行](#5-运行)
6. [使用（网页 / API 两种方式）](#6-使用)
7. [测试与验证](#7-测试与验证)
8. [避坑指南（真实踩过的坑）](#8-避坑指南)
9. [配置参考](#9-配置参考)
10. [常见问题 FAQ](#10-常见问题-faq)

---

## 1. 这是什么 / 主要构思

SongForge 是一个 **AI 歌曲生成器**的后端 + 前端完整实现，定位是**要上线收费的生产级项目**，
而不是 demo。核心构思有五条：

1. **零门槛**：首屏只有一个输入框 + 两个上传按钮。不出现 BPM、调性、压缩比这类术语；
   高级参数全部收在可折叠的"高级选项"里，默认不展开。
2. **快反馈**：不是让你干等一条进度条。管线会**先把副歌钩子段（30 秒）渲染出来并立刻推送**
   （目标 ≤ 25 秒内能听到），同时全曲在后台继续生成。
3. **成品级**：输出不是"AI 音频片段"。有 intro / verse / chorus / bridge / outro 结构，
   响度校准到流媒体标准（默认 **-14 LUFS / -1 dBTP**），无爆音、无截断，
   附 WAV + MP3 + 分轨 + 歌词，可直接发行。
4. **可插拔的音乐引擎**：默认内置一个**确定性的 DSP 合成引擎**（SimulatedProvider），
   零外部依赖、结果可复现、成本可控，保证整条管线随时能跑、能测。
   接真实模型（Suno / OpenAudio 风格的 REST 网关）只需改环境变量，一行不用改代码。
5. **生产三件套**：**可观测**（结构化 JSON 日志 + Prometheus 指标 + 可选 OTel 追踪）、
   **可重试**（每个管线阶段有界重试 + 指数退避）、**可控成本**（每阶段计费入账 +
   单任务预算 + 24 小时日成本双闸门，超支直接中止且不再花钱）。

### MVP 明确不做（请也别"顺手"实现）

DAW 级多轨编辑器、MIDI 导出编辑、多人协作、自动发行到 Spotify、移动原生 App、模型自训练。

---

## 2. 三条产品承诺

| 承诺 | 怎么做到 | 怎么验证 |
|---|---|---|
| 零门槛 | 前端首屏只有一个输入框 + 两个上传按钮；术语全在折叠面板 | 打开 :5173 看首屏 |
| 快反馈（≤25s 听到钩子） | 管线先跑 `preview` 阶段（只合成副歌 + 轻母带）→ 推 `preview_ready` 事件 | `smoke_test.py` 的 SLO 检查项；`/metrics` 里的 `sf_preview_latency_seconds` |
| 成品级 | 结构化段落合成 → 混音 → 两级母带（进程内首遍 + ffmpeg `ebur128` 权威校准）→ MP3/分轨/歌词 | `smoke_test.py` 12/12 项检查 |

## 3. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│ 前端 (React + Vite)                                              │
│   输入页: ①一句话 ②录音 ③上传 loop      结果页: 进度+预览+下载+歌词 │
└──────────────────────────────┬──────────────────────────────────┘
                               │  REST /v1  +  WebSocket /ws/jobs/{id}
┌──────────────────────────────┴──────────────────────────────────┐
│  FastAPI 应用                                                    │
│   路由: 建任务/上传/列表/详情/取消/下载/健康检查/metrics           │
│   Worker(内嵌线程或独立进程): 原子领取任务 → 交给编排器             │
│   EventBus: stage / preview_ready / succeeded / failed 事件      │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────────┐
│  管线编排器（每阶段: 重试 + 成本闸门 + 事件）                        │
│   intent → lyrics → preview(≤25s SLO) → compose → mix_master    │
│            → export_mp3                                          │
│                                                                 │
│   音乐 Provider（可插拔）:                                        │
│     SimulatedProvider  内置确定性合成（默认，零依赖）               │
│     HttpMusicProvider  真实模型 REST 网关（配 SF_PROVIDER=http）  │
│                                                                 │
│   DSP 引擎: 混音 / 母带(R128 LUFS + BS.1770 真峰, ffmpeg 校准) /   │
│             节拍分析 / 声纹特征 / MP3 导出                          │
│   持久化: SQLAlchemy（Job / StageAttempt / CostLedger）           │
└─────────────────────────────────────────────────────────────────┘
```

**关键设计解释**（小白也能懂）：

- **两级母带**：第一遍在进程内做完压缩/高通/归一/限幅（不依赖任何外部工具，测试随时能跑）；
  第二遍用 ffmpeg 的 `ebur128` 滤波器（和流媒体平台用的同一套标准）**实测**整曲响度，
  精确补增益到目标值。没有 ffmpeg 时自动降级只跑第一遍，不阻塞。
- **原子领取任务**：worker 用条件 UPDATE（`WHERE status='queued'`）抢任务，
  即使跑多个 worker 容器也不会重复处理同一个任务。
- **成本闸门在花钱之前检查**：先查预算再入账，被闸门拦下的任务一分钱都不会多花。

## 4. 安装指南

### 4.1 你需要准备的东西（全部免费）

| 工具 | 用途 | 安装 |
|---|---|---|
| **Python 3.10+** | 后端 | python.org 下载，安装时**勾选 "Add to PATH"** |
| **ffmpeg** | MP3 编码 + 响度校准 | `winget install Gyan.FFmpeg`（Windows）或包管理器 |
| **Node.js 18+** | 前端 | nodejs.org（LTS 版即可） |

装完在 PowerShell 里验证（三条都有版本号输出就 OK）：

```powershell
python --version    # 例如 Python 3.11.x
ffmpeg -version     # 第一行有版本即可
node --version      # 例如 v22.x
```

> 没有 ffmpeg 也能跑！只是 MP3 导出和响度校准降级，WAV 成品照常输出。
> 但想验证"成品级"承诺的话建议装。

### 4.2 克隆 / 获取代码

```powershell
# 如果你已经本地有这个文件夹，跳过这一步
git clone <你的仓库地址> songforge
cd D:\aistudypython\songforge
```

### 4.3 装后端依赖

```powershell
cd D:\aistudypython\songforge
pip install -r requirements.txt -r requirements-dev.txt
```

### 4.4 装前端依赖

```powershell
cd D:\aistudypython\songforge\frontend
npm install
```

> 第一次 `npm install` 可能要几分钟（下载 React/Vite 包），正常现象，不是卡死。
> 网络慢可以换源：`npm config set registry https://registry.npmmirror.com`

### 4.5 （可选）配置环境变量

默认配置已经能完整跑通（内置合成引擎，零外部依赖），**可以完全跳过这步**。
要接真实音乐模型或改响度目标时再建 `.env`：

```powershell
copy .env.example .env
```

然后按需编辑 `.env`（所有配置项见第 9 节）。

## 5. 运行

**开两个终端**。

终端 1 —— 启动后端（API + 内嵌 worker）：

```powershell
cd D:\aistudypython\songforge\backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

看到下面三行就成功了：

```
songforge starting v0.1.0 provider=simulated worker=embedded
worker starting (poll=0.5s)
Uvicorn running on http://127.0.0.1:8000
```

终端 2 —— 启动前端：

```powershell
cd D:\aistudypython\songforge\frontend
npm run dev
```

**浏览器打开 http://localhost:5173**，看到"给我一个念头，还你一首能直接发布的歌"
+ 三个入口卡片，就跑起来了。

> Vite 开发服务器已把 `/v1` 和 `/ws` 代理到 8000 端口，所以不需要配 CORS、
> 不需要改任何前端代码。

### Docker 方式（生产拓扑）

```powershell
cd D:\aistudypython\songforge
docker compose up --build
```

API 在 :8000，内嵌 worker 同容器；ffmpeg 已内置进镜像；
SQLite 与存储落在 `sf-data` 卷。横向扩展 worker 时：
再跑一个同样镜像、`SF_ENABLE_WORKER=true` 的容器，
API 容器设 `SF_ENABLE_WORKER=false`——原子领取保证任务不重复。

## 6. 使用

### 6.1 网页方式

首屏选一个入口：

1. **一句话描述**：写"深夜雨声里想念旧朋友的 LoFi 小调，副歌要抓耳"这种就行，
   不用写术语。点「生成歌曲」。
2. **录一段声音**：点录音按钮录 3–30 秒（注意浏览器会要麦克风权限），
   系统提取你的音色特征塑形人声轨，并按你声音的音高区间对齐旋律。
3. **上传节拍 / loop**：拖一段 wav/mp3，系统分析它的 BPM / 调性 / 能量，
   生成的歌会和它"对齐"（同 BPM、同调性）。

提交后进入结果页：先出 **30 秒钩子预览**（可立刻听），全曲完成后出现
成品播放器 + 下载区（master.wav / master.mp3 / 5 组分轨 / 歌词.txt）。

### 6.2 API 方式（curl / 脚本）

```powershell
# 1) 文字入口创建任务
$body = '{"description":"lofi 雨夜想念旧朋友","input_type":"text","duration_secs":90,"language":"zh"}'
$r = Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/jobs" -Method Post -Body $body -ContentType "application/json"
$jobId = $r.job_id

# 2) 轮询状态（或开 WebSocket /ws/jobs/$jobId 收进度事件）
while ($true) {
  $j = Invoke-RestMethod "http://127.0.0.1:8000/v1/jobs/$jobId"
  if ($j.status -in "succeeded","failed","cancelled") { break }
  Start-Sleep 2
}

# 3) 下载成品
Invoke-WebRequest "http://127.0.0.1:8000/v1/jobs/$jobId/files/master.mp3" -OutFile master.mp3
```

语音 / loop 入口（**元数据是 multipart 表单字段，不是 URL query 参数**）：

```powershell
$clip = "C:\path\to\clip.wav"
curl.exe -F "file=@$clip" -F "description=gentle lofi" -F "duration_secs=90" -F "language=zh" http://127.0.0.1:8000/v1/jobs/voice
```

全部端点：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/jobs` | 文字输入 |
| POST | `/v1/jobs/voice` · `/v1/jobs/loop` | multipart 上传（file + 表单字段） |
| GET | `/v1/jobs` · `/v1/jobs/{id}` | 列表 / 详情（含资产、歌词、成本） |
| POST | `/v1/jobs/{id}/cancel` | 取消排队中的任务 |
| GET | `/v1/jobs/{id}/files/{name}` | 下载 preview.wav / master.wav / master.mp3 / stems/*.wav / lyrics.txt |
| WS | `/ws/jobs/{id}` | 进度流 |
| GET | `/healthz` · `/metrics` | 存活 · Prometheus 指标 |

> 配了 `SF_API_KEY` 后，`/v1` 全部要求 `X-API-Key` 请求头（WebSocket 可用 `?api_key=` 查询参数，
> 因为浏览器 WS 握手不能自定义 header）。收费上线前**一定**要配。

## 7. 测试与验证

```powershell
cd D:\aistudypython\songforge

# 单元 + 集成测试（DSP / 编排器 / API / 回归，41 项）
python -m pytest backend/tests -q

# 端到端冒烟：真实走完一个任务，逐条校验产品承诺（需 ffmpeg）
python backend/smoke_test.py
# 期望输出: 12/12 checks passed
```

冒烟测试校验的 12 项包括：任务成功、master.wav 存在、时长精确（无截断）、
**ffmpeg 实测 LUFS ≈ -14**、**真峰 ≤ -1 dBTP（无爆音）**、MP3 有效、5 组分轨、
歌词段落结构、钩子预览 SLO、`preview_ready` 事件、成本入账、预算未超。

## 8. 避坑指南

以下是本项目开发过程中**真实踩过**的坑，按被踩概率排序：

1. **端口 5173 被残留 node 进程占用**（最高发）
   症状：`npm run dev` 终端长时间空白，或浏览器 5173 拒绝连接。
   新版 Vite 此时会**静默换到 5174**，你打开的还是 5173 → 看起来"跑不起来"。
   解决：
   ```powershell
   Get-Process -Name node | Stop-Process -Force
   Get-NetTCPConnection -LocalPort 5173 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
   ```
   本仓库的 `vite.config.js` 已设 `strictPort: true`——端口被占时**直接报错退出**，不再静默换端口。

2. **Vite 只监听 IPv6 `::1`**：`http://127.0.0.1:5173` 不通但 `http://[::1]:5173` 通。
   已修：`vite.config.js` 里 `host: true`（监听双栈）。别把这个删了。

3. **上传元数据丢失（前后端契约）**：浏览器 `FormData` 把字段发成 multipart **表单字段**，
   如果后端参数声明成 `Query` 就会静默取不到值（description 变 None、时长回落默认）。
   本仓库已统一为 `Form`，并有回归测试 `test_frontend_contract.py` 锁死。
   **改这两个端点时千万别改回 Query。**

4. **PowerShell 里 `pip` / `npm` 的 stderr 被当错误**：
   看到 `Command exited with code 1` 但实际装成功了（pip 的 upgrade notice 走 stderr）。
   以"命令末尾的实际输出"和"依赖是否可导入"为准，不要只看退出码。

5. **ffmpeg 的 ebur128 输出字段名**：真峰字段是 **`TPK:`**（true peak）和 `FTPK:`，
   不是 `TP:`；集成响度是汇总行的 `I: -xx LUFS`。正则写错会取到 `-inf` 地板值，
   "无爆音"检查变成假通过。`app/dsp/loudness.py` 里的 `measure_lufs_ffmpeg` 已处理。

6. **逐样本 Python 循环做 DSP 会慢到不可用**：90 秒 × 44100 Hz = 400 万样本，
   任何 `for i in range(n)` 的包络/限幅循环都要向量化（numpy 滑窗、scipy SOS 滤波）。
   本仓库 DSP 全部是 numpy/scipy 向量化实现，全管线 simulated 模式下 < 5 秒出 90 秒成品。

7. **BS.1770 K 加权 shelf 滤波器的直接型系数在 44.1kHz 下数值不稳定**（极点跑出单位圆 → NaN）。
   所以本项目**不在进程内手搓 shelf**，用"简化 K 加权做首遍 + ffmpeg ebur128 做权威校准"的两级方案。
   别"优化"成进程内全量 R128，会引入 NaN。

8. **`Session.get()` 漏传类参数**：`s.get(job_id)` 会炸 `missing positional argument 'ident'`，
   必须是 `s.get(Job, job_id)`。

9. **token / 密钥进仓库**：`.env`、`.data/`、`*.db` 都已在 `.gitignore` 里。
   **永远不要把 `SF_API_KEY`、GitHub token、模型 API key 提交进代码库。**

10. **取消任务不会立刻停掉已运行的任务**：cancel 只拦截排队中的任务；
    正在跑的任务会跑完当前阶段（设计如此，避免半截文件）。要彻底中断需重启 worker。

## 9. 配置参考

所有配置走环境变量或 `.env`（`SF_` 前缀，pydantic-settings 自动映射）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SF_PROVIDER` | `simulated` | 换 `http` 接真实音乐模型 |
| `SF_PROVIDER_BASE_URL` / `SF_PROVIDER_API_KEY` | 空 | 真实模型 REST 网关 |
| `SF_LLM_BASE_URL` / `SF_LLM_API_KEY` / `SF_LLM_MODEL` | 空 | 可选 LLM（意图/歌词），不配走确定性回退 |
| `SF_TARGET_LUFS` / `SF_TRUE_PEAK_DB` | -14.0 / -1.0 | 流媒体响度目标 |
| `SF_PREVIEW_SECONDS` / `SF_PREVIEW_SLO_SECONDS` | 30 / 25 | 钩子时长 / SLO |
| `SF_JOB_BUDGET_USD` / `SF_DAILY_COST_CAP_USD` | 2.00 / 500.0 | 成本闸门 |
| `SF_PRICING_PREVIEW_USD` / `SF_PRICING_FULL_USD` / `SF_PRICING_MIX_MASTER_USD` | 0.02 / 0.15 / 0.01 | 阶段计费 |
| `SF_MAX_STAGE_ATTEMPTS` / `SF_STAGE_RETRY_BASE_DELAY_S` / `SF_STAGE_RETRY_MAX_DELAY_S` | 3 / 1.0 / 15.0 | 重试策略 |
| `SF_API_KEY` | 空 | 设了之后 `/v1` 全要鉴权 |
| `SF_DATABASE_URL` | `sqlite:///.data/songforge.db` | 生产建议 PostgreSQL |
| `SF_STORAGE_DIR` | `.data/storage` | 音频产物根目录 |
| `SF_ENABLE_WORKER` / `SF_WORKER_CONCURRENCY` | true / 2 | 内嵌 worker 开关 / 并发 |
| `SF_OTEL_ENDPOINT` | 空 | 可选 OTLP 追踪上报 |

## 10. 常见问题 FAQ

**Q: 能直接听生成的歌吗？**
能。`master.wav` 是 44.1kHz 立体声 PCM16，任何播放器都行；`master.mp3` 是 320kbps。

**Q: 生成的歌"听起来像 AI 合成的方块音"，正常吗？**
默认 `simulated` provider 是**工程验证用的确定性合成器**（振荡器 + ADSR + 噪声鼓），
用来保证管线/母带/导出全链路正确且可复现。听感是"像歌的结构"而非成品人声质感。
接入真实模型（`SF_PROVIDER=http`）后听感才到"成品级"。

**Q: 为什么预览和全曲是两次生成而不是全曲的前 30 秒？**
预览阶段只合成副歌段（更快、更省），全曲阶段按 section 权重完整合成一遍。
这样钩子能在 SLO 内给出，且全曲质量不受"预览加速"影响。

**Q: 数据库里会不会越存越多？**
会。任务行 + 音频产物都在 `.data/` 下。生产环境记得配存储清理策略（MVP 未内置）。

**Q: 如何加一种新的输入方式？**
在 `app/api/routes.py` 加端点（复用 `_create_audio_job` 模式）→ `Job.input_type` 加枚举 →
编排器 `_load_context` 里加对应的 ingest 分支。管线其余部分不用动。

---

<div align="center">

*—— 署名：由 Agnes-3.0-flash（Sapiens AI 出品）构建并署名*

</div>
