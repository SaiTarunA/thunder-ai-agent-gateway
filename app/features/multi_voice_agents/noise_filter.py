"""Server-side noise suppression for the caller's microphone (no LiveKit Cloud needed).

RNNoise (a small recurrent network, run locally through pyrnnoise) cleans every incoming audio
frame before it reaches the realtime model. On top of that a speech-probability gate mutes the
audio while RNNoise thinks nobody is speaking, so taps, breaths and room sounds near the mic
no longer look like speech to the model's voice detector and stop the agent mid-sentence.

Plugged in through room_io.AudioInputOptions(noise_cancellation=...), which accepts any
rtc.FrameProcessor. Frames arrive mono at the session's input rate (24 kHz by default); RNNoise
needs 48 kHz, so 24 kHz audio is upsampled and downsampled again around it (exact 2x, so frame
sizes never change).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from livekit import rtc

log = logging.getLogger("noise_filter")

RNNOISE_RATE = 48000
SUPPORTED_RATES = (24000, 48000)


class RNNoiseProcessor(rtc.FrameProcessor[rtc.AudioFrame]):
    def __init__(
        self,
        *,
        gate_threshold: float = 0.35,
        gate_hold_ms: int = 200,
        gate_floor: float = 0.0,
    ) -> None:
        # Imported here so a missing or broken install disables the filter instead of the worker.
        from pyrnnoise.rnnoise import FRAME_SIZE, create, destroy, process_mono_frame

        self._frame_size = FRAME_SIZE  # 480 samples = 10 ms at 48 kHz
        self._process_mono = process_mono_frame
        self._destroy = destroy
        self._state = create()
        self._enabled = True
        self._in = np.zeros(0, dtype=np.int16)
        self._out = np.zeros(self._frame_size, dtype=np.int16)  # 10 ms of look-ahead latency
        self._prev_sample = 0.0  # keeps the 2x upsampler continuous across frames
        self._gate_threshold = gate_threshold
        self._hold_frames = max(1, gate_hold_ms // 10)
        self._gate_floor = gate_floor
        self._hold_left = 0
        self._warned_rate = False

    # ---- rtc.FrameProcessor --------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def _process(self, frame: rtc.AudioFrame) -> rtc.AudioFrame:
        if not self._enabled or self._state is None:
            return frame
        if frame.num_channels != 1 or frame.sample_rate not in SUPPORTED_RATES:
            if not self._warned_rate:
                self._warned_rate = True
                log.warning(
                    "noise filter skipped: needs mono 24/48 kHz audio, got %d ch @ %d Hz",
                    frame.num_channels,
                    frame.sample_rate,
                )
            return frame

        n = frame.samples_per_channel
        pcm = np.frombuffer(frame.data, dtype=np.int16)[:n]
        wide = self._upsample(pcm) if frame.sample_rate == 24000 else pcm
        out48 = self._run(wide)
        out = self._downsample(out48) if frame.sample_rate == 24000 else out48
        return rtc.AudioFrame(
            data=out.astype(np.int16).tobytes(),
            sample_rate=frame.sample_rate,
            num_channels=1,
            samples_per_channel=n,
        )

    def _close(self) -> None:
        if self._state is not None:
            self._destroy(self._state)
            self._state = None

    # ---- internals -----------------------------------------------------------------------
    def _run(self, wide: np.ndarray) -> np.ndarray:
        """Denoise + gate 48 kHz audio. Returns exactly len(wide) samples (delayed by 10 ms)."""
        want = len(wide)
        self._in = np.concatenate((self._in, wide))
        chunks: list[np.ndarray] = []
        while len(self._in) >= self._frame_size:
            chunk, self._in = self._in[: self._frame_size], self._in[self._frame_size :]
            clean, prob = self._process_mono(self._state, chunk)
            chunks.append(self._gate(clean, float(prob)))
        if chunks:
            self._out = np.concatenate((self._out, *chunks))
        out, self._out = self._out[:want], self._out[want:]
        return out

    def _gate(self, clean: np.ndarray, prob: float) -> np.ndarray:
        if prob >= self._gate_threshold:
            self._hold_left = self._hold_frames  # speech: open, and stay open through word tails
            return clean
        if self._hold_left > 0:
            self._hold_left -= 1
            return clean
        return (clean * self._gate_floor).astype(np.int16)

    def _upsample(self, pcm: np.ndarray) -> np.ndarray:
        x = pcm.astype(np.float32)
        prev = np.concatenate(([self._prev_sample], x[:-1])) if len(x) else x
        mid = (prev + x) / 2.0
        up = np.empty(len(x) * 2, dtype=np.float32)
        up[0::2] = mid
        up[1::2] = x
        if len(x):
            self._prev_sample = float(x[-1])
        return up.astype(np.int16)

    @staticmethod
    def _downsample(wide: np.ndarray) -> np.ndarray:
        pairs = wide.astype(np.float32).reshape(-1, 2)
        return pairs.mean(axis=1)


def build_noise_filter(settings: Any) -> RNNoiseProcessor | None:
    """The configured filter, or None when disabled or unavailable (the call still works)."""
    if str(getattr(settings, "noise_cancellation", "rnnoise")).lower() in ("", "none", "off"):
        return None
    try:
        return RNNoiseProcessor(
            gate_threshold=settings.noise_gate_threshold,
            gate_hold_ms=settings.noise_gate_hold_ms,
        )
    except Exception:
        log.exception("noise filter unavailable, continuing without it")
        return None
