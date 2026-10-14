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
from collections import deque
from typing import Any

import numpy as np
from livekit import rtc

log = logging.getLogger("noise_filter")

RNNOISE_RATE = 48000
RNNOISE_DELAY_FRAMES = 2  # measured: RNNoise output lags input by 20 ms (2 x 10 ms frames)
ONSET_FRAMES = 4  # frames kept unfiltered after the gate opens: RNNoise still dampens a word's first ~40 ms
SUPPORTED_RATES = (24000, 48000)
CONFIDENT_PROB = 0.8  # RNNoise speech probability that counts as clear speech for level learning
CALIBRATION_FRAMES = 50  # 10 ms frames of confident speech (~0.5 s) before the level gate arms
LEVEL_ALPHA = 0.02  # how fast the learned speaking level follows the caller


class RNNoiseProcessor(rtc.FrameProcessor[rtc.AudioFrame]):
    def __init__(
        self,
        *,
        gate_threshold: float = 0.5,
        gate_close_threshold: float = 0.3,
        gate_hold_ms: int = 200,
        gate_lookahead_ms: int = 80,
        near_field_ratio: float = 0.3,
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
        # The gate looks ahead so it can open *before* a word starts (soft sounds like "h" or "f"
        # are only recognised as speech a few frames in). Frames wait in _queue until their
        # look-ahead window is known; the output is that many frames late, so it starts padded.
        self._look_frames = max(0, gate_lookahead_ms // 10)
        # (cleaned, original, speech prob, cleaned rms)
        self._queue: deque[tuple[np.ndarray, np.ndarray, float, float]] = deque()
        self._out = np.zeros((self._look_frames + 1) * self._frame_size, dtype=np.int16)
        self._prev_sample = 0.0  # keeps the 2x upsampler continuous across frames
        self._gate_threshold = gate_threshold  # speech probability needed to OPEN the gate
        self._close_threshold = min(gate_close_threshold, gate_threshold)  # ...and to keep it open
        # Near-field gate: learn how loud the caller normally speaks and refuse to open for voices
        # much quieter than that (a person 4 feet away, TV, a conversation across the room).
        self._near_ratio = near_field_ratio  # 0 turns it off
        self._user_level: float | None = None  # typical RMS of the caller's confident speech
        self._calib_n = 0
        self._calib_sum = 0.0
        self._open = False
        self._crossed = False
        self._hold_frames = max(1, gate_hold_ms // 10)
        self._gate_floor = gate_floor
        self._history: deque[float] = deque(maxlen=self._hold_frames)  # probs of frames already sent
        # RNNoise's output lags its input by 20 ms; unfiltered frames are delayed the same amount
        # so the two streams line up when the gate switches between them.
        self._onset_left = 0
        self._orig_delay: deque[np.ndarray] = deque(
            [np.zeros(self._frame_size, dtype=np.int16)] * RNNOISE_DELAY_FRAMES
        )
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
            self._orig_delay.append(chunk)
            rms = float(np.sqrt(np.mean(clean.astype(np.float32) ** 2)))
            self._queue.append((clean, self._orig_delay.popleft(), float(prob), rms))
            while len(self._queue) > self._look_frames:
                chunks.append(self._release())
        if chunks:
            self._out = np.concatenate((self._out, *chunks))
        out, self._out = self._out[:want], self._out[want:]
        return out

    def _release(self) -> np.ndarray:
        """Send the oldest queued frame through the gate.

        Opening needs speech probability >= the open threshold (seen up to look-ahead ms early)
        AND enough level for the near-field check. Once open it stays open until the probability
        stays under the lower close threshold for hold_ms (hysteresis), so word tails survive.
        RNNoise only recognises speech a few frames after a word starts and mutes those first
        frames, which is where soft sounds ("h", "f", "th") live, so frames from the moment the
        gate opens until a few frames past the first confident one go out unfiltered; the rest
        goes out cleaned; everything while the gate is closed is muted.
        """
        clean, original, prob, rms = self._queue.popleft()
        recent = max(self._history, default=0.0)
        ahead_prob = max((q[2] for q in self._queue), default=0.0)
        peak = max([rms] + [q[3] for q in self._queue])  # loudest frame in the look-ahead window
        self._history.append(prob)

        if self._open:
            if max(prob, recent) < self._close_threshold:
                self._open = False
        elif max(prob, ahead_prob) >= self._gate_threshold and self._level_ok(peak):
            self._open, self._crossed, self._onset_left = True, False, 0

        if not self._open:
            return (clean * self._gate_floor).astype(np.int16)

        if not self._crossed:
            if prob < self._gate_threshold:
                return original  # pre-roll: the onset RNNoise has not recognised yet
            self._crossed, self._onset_left = True, ONSET_FRAMES
        if self._onset_left > 0:
            self._onset_left -= 1
            return original
        self._learn_level(prob, peak)
        return clean

    def _level_ok(self, rms: float) -> bool:
        """False for speech far quieter than the caller's usual level (a distant talker)."""
        if self._near_ratio <= 0 or self._user_level is None:
            return True
        return rms >= self._user_level * self._near_ratio

    def _learn_level(self, prob: float, rms: float) -> None:
        """Track the caller's own speaking level (window peak, like _level_ok) from confident speech."""
        if self._near_ratio <= 0 or prob < CONFIDENT_PROB:
            return
        if self._user_level is None:  # calibrate on the first ~0.5 s of confident speech
            self._calib_n += 1
            self._calib_sum += rms
            if self._calib_n >= CALIBRATION_FRAMES:
                self._user_level = self._calib_sum / self._calib_n
            return
        if rms >= self._user_level * 0.5:  # ignore quieter voices so the level cannot drift to them
            self._user_level += LEVEL_ALPHA * (rms - self._user_level)

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
            gate_close_threshold=settings.noise_gate_close_threshold,
            gate_hold_ms=settings.noise_gate_hold_ms,
            gate_lookahead_ms=settings.noise_gate_lookahead_ms,
            near_field_ratio=settings.noise_near_field_ratio,
        )
    except Exception:
        log.exception("noise filter unavailable, continuing without it")
        return None
