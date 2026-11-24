"""Audio DSP & synthesis engine.

Submodules:
- wav_io        : WAV read/write (PCM16, mono/stereo)
- core          : numpy DSP primitives (convolve, envelope, EQ, compressor)
- loudness      : EBU R128 / BS.1770 K-weighted LUFS + true peak
- mastering     : two-pass mixdown + mastering chain
- synthesis     : deterministic seeded song renderer (SimulatedProvider back-end)
- beat_analysis : BPM / key / structure estimation from a loop
- voiceprint    : timbre feature extraction from a voice clip
- encoders      : ffmpeg-backed MP3 encode / arbitrary-decode helpers
"""
