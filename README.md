# SongForge — 一句话，还你一首能直接发布的歌

<div align="center">

[English](./README.en.md#top) · [简体中文](./README.zh.md#top) · [日本語](./README.ja.md#top) · [한국어](./README.ko.md#top)

</div>

**AI 歌曲生成器**：给一个念头（一句话 / 一段你自己的声音 / 一段节拍 loop），
几分钟内拿到**已混音、已母带、无爆音、响度达标（-14 LUFS / -1 dBTP）**的成品歌曲：
`master.wav` + `master.mp3` + 5 组分轨（drums / bass / chords / melody / vox）+ 带段落标注的歌词。
可直接上传流媒体平台。

> 定位：要上线收费的**生产级**项目，不是 demo。

## 30 秒跑起来

```powershell
# 前置: Python 3.10+ / ffmpeg / Node 18+ (详见 README.zh.md §4)

# 后端 (API + 内嵌 worker)
pip install -r requirements.txt
cd backend && python -m uvicorn app.main:app --port 8000

# 前端 (另一个终端)
cd frontend && npm install && npm run dev
# 打开 http://localhost:5173
```

## 验证

```powershell
python -m pytest backend/tests -q     # 41 passed
python backend/smoke_test.py          # 12/12 checks passed (需 ffmpeg)
```

## 架构速览

```
React SPA ──REST /v1 + WS /ws──> FastAPI ──> Worker(内嵌/独立, 原子领取)
   管线: intent → lyrics → preview(≤25s SLO) → compose → mix_master → export
   音乐引擎: SimulatedProvider(默认, 零依赖) / HttpMusicProvider(真实模型网关)
   DSP: 混音 + 两级母带(进程内首遍 + ffmpeg ebur128 权威校准) + 分轨/歌词/MP3
   生产三件套: 可观测(JSON日志+Prometheus+OTel) · 可重试(有界+指数退避) · 可控成本(双闸门)
```

## 目录

```
backend/   FastAPI + 管线编排 + DSP 引擎 + Provider 抽象 + 测试(41) + smoke_test
frontend/  React + Vite SPA (三入口输入页 + 进度/预览/下载结果页)
Dockerfile · docker-compose.yml · Makefile · .env.example · requirements*.txt
```

## 三条产品承诺

| 承诺 | 实现 | 验证 |
|---|---|---|
| **零门槛** | 首屏只有输入框 + 两个上传按钮，术语全收进折叠面板 | 打开 :5173 |
| **快反馈** | 先渲染副歌钩子 → 立即推 `preview_ready`（≤ 25 s SLO） | `smoke_test.py` + `sf_preview_latency_seconds` |
| **成品级** | 完整段落结构 + -14 LUFS/-1 dBTP + WAV/MP3/分轨/歌词，无爆音无截断 | `smoke_test.py` 12/12 |

**MVP 明确不做**：DAW 多轨编辑、MIDI 导出、多人协作、自动发行 Spotify、移动原生 App、模型自训练。

---

<div align="center">

— Signed: built and signed off by Agnes-3.0-flash (Sapiens AI)

</div>
