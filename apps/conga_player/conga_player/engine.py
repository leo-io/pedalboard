"""Audio engine for conga_player using pedalboard AudioStream."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Optional
import numpy as np

from pedalboard import Gain, Pedalboard
from pedalboard.io import AudioStream


@dataclass
class EngineState:
    position_s: float
    playing: bool
    loop_enabled: bool
    loop_start_s: float
    loop_end_s: float
    gain_db: float
    frames_written: int


class Engine:
    def __init__(
        self,
        audio: np.ndarray,
        sample_rate: int,
        *,
        buffer_size: int = 2048,
        output_device_name: Optional[str] = None,
        declick_ms: float = 2.0,
        latency_offset_ms: Optional[float] = None,
    ) -> None:
        if audio.ndim == 1:
            audio = np.stack([audio, audio], axis=0)
        elif audio.ndim == 2 and audio.shape[0] > audio.shape[1]:
            # Transpose if passed (frames, channels)
            audio = audio.T

        self.audio = np.ascontiguousarray(audio, dtype=np.float32)
        self.num_channels = self.audio.shape[0]
        self.total_frames = self.audio.shape[1]
        self.sample_rate = int(sample_rate)
        self.buffer_size = int(buffer_size)
        self.output_device_name = output_device_name
        self.declick_ms = float(declick_ms)
        self.latency_offset_ms = latency_offset_ms

        self._lock = threading.Lock()
        self._pos_frame = 0
        self._playing = False
        self._loop_enabled = False
        self._loop_start_frame = 0
        self._loop_end_frame = self.total_frames
        self._gain_db = 0.0
        self._frames_written = 0

        self._last_write_mono = time.monotonic()
        self._last_write_song_frame = 0
        self._last_write_playing = False

        self._quit = False
        self._thread: Optional[threading.Thread] = None
        self._next_write = 0.0

        self.gain_plugin = Gain(gain_db=0.0)
        self.board = Pedalboard([self.gain_plugin])

    def start(self) -> None:
        """Start the background audio feeder thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._quit = False
        self._thread = threading.Thread(target=self._feed_loop, daemon=True, name="AudioFeeder")
        self._thread.start()

    def close(self) -> None:
        """Stop playback and terminate the feeder thread."""
        with self._lock:
            self._quit = True
            self._playing = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def play(self) -> None:
        """Resume playback."""
        with self._lock:
            self._playing = True
            self._last_write_mono = time.monotonic()
            self._last_write_song_frame = self._pos_frame
            self._last_write_playing = True

    def pause(self) -> None:
        """Pause playback."""
        with self._lock:
            self._playing = False
            self._last_write_playing = False

    def toggle(self) -> None:
        """Toggle playback between play and pause."""
        with self._lock:
            if self._playing:
                self._playing = False
                self._last_write_playing = False
            else:
                self._playing = True
                self._last_write_mono = time.monotonic()
                self._last_write_song_frame = self._pos_frame
                self._last_write_playing = True

    def stop(self) -> None:
        """Stop playback and rewind to beginning."""
        with self._lock:
            self._playing = False
            self._last_write_playing = False
            self._pos_frame = 0
            self._last_write_song_frame = 0

    def seek(self, seconds: float) -> None:
        """Seek to a position in seconds."""
        with self._lock:
            frame = max(0, min(int(round(seconds * self.sample_rate)), self.total_frames))
            self._pos_frame = frame
            self._last_write_mono = time.monotonic()
            self._last_write_song_frame = frame

    def set_loop(self, start_s: float, end_s: float) -> None:
        """Set loop boundaries in seconds and enable looping."""
        with self._lock:
            start_frame = max(0, min(int(round(start_s * self.sample_rate)), self.total_frames))
            end_frame = max(0, min(int(round(end_s * self.sample_rate)), self.total_frames))
            if start_frame > end_frame:
                start_frame, end_frame = end_frame, start_frame
            self._loop_start_frame = start_frame
            self._loop_end_frame = end_frame
            self._loop_enabled = True

    def clear_loop(self) -> None:
        """Clear loop boundaries and disable looping."""
        with self._lock:
            self._loop_enabled = False
            self._loop_start_frame = 0
            self._loop_end_frame = self.total_frames

    def set_loop_enabled(self, enabled: bool) -> None:
        """Enable or disable looping without changing boundaries."""
        with self._lock:
            self._loop_enabled = bool(enabled)

    def set_gain_db(self, db: float) -> None:
        """Set output volume gain in decibels."""
        with self._lock:
            self._gain_db = float(db)
            self.gain_plugin.gain_db = self._gain_db

    def state(self) -> EngineState:
        """Query engine state with smooth position interpolation."""
        with self._lock:
            playing = self._playing
            pos_frame = self._pos_frame
            loop_enabled = self._loop_enabled
            loop_start_s = self._loop_start_frame / float(self.sample_rate)
            loop_end_s = self._loop_end_frame / float(self.sample_rate)
            gain_db = self._gain_db
            frames_written = self._frames_written
            last_mono = self._last_write_mono
            last_song_frame = self._last_write_song_frame
            last_playing = self._last_write_playing
            l_start = self._loop_start_frame
            l_end = self._loop_end_frame

        latency_frames = (
            int(round(self.latency_offset_ms * self.sample_rate / 1000.0))
            if self.latency_offset_ms is not None
            else self.buffer_size
        )

        if playing and last_playing:
            now = time.monotonic()
            elapsed = now - last_mono
            cur_frame = last_song_frame + int(round(elapsed * self.sample_rate)) - latency_frames
            if loop_enabled and (l_end > l_start):
                loop_len = l_end - l_start
                cur_frame = l_start + ((cur_frame - l_start) % loop_len)
            else:
                cur_frame = max(0, min(cur_frame, self.total_frames))
            pos_s = cur_frame / float(self.sample_rate)
        else:
            pos_s = pos_frame / float(self.sample_rate)

        return EngineState(
            position_s=pos_s,
            playing=playing,
            loop_enabled=loop_enabled,
            loop_start_s=loop_start_s,
            loop_end_s=loop_end_s,
            gain_db=gain_db,
            frames_written=frames_written,
        )

    def _next_block(self, n: int) -> np.ndarray:
        """Assemble the next block of audio frames.

        Pure and lock-protected: safe to drive directly in tests without an AudioStream.
        """
        with self._lock:
            song_frame_at_start = self._pos_frame
            was_playing = self._playing

            if not self._playing:
                self._last_write_song_frame = song_frame_at_start
                self._last_write_mono = time.monotonic()
                self._last_write_playing = False
                self._frames_written += n
                return np.zeros((self.num_channels, n), dtype=np.float32)

            out = np.zeros((self.num_channels, n), dtype=np.float32)
            written = 0
            declick_frames = int(round(self.declick_ms * self.sample_rate / 1000.0))
            wrap_seams: list[int] = []

            is_looping = self._loop_enabled and (self._loop_end_frame > self._loop_start_frame)

            while written < n and self._playing:
                if is_looping:
                    if self._pos_frame < self._loop_start_frame or self._pos_frame >= self._loop_end_frame:
                        self._pos_frame = self._loop_start_frame

                    available = self._loop_end_frame - self._pos_frame
                    take = min(n - written, available)
                    out[:, written : written + take] = self.audio[:, self._pos_frame : self._pos_frame + take]
                    self._pos_frame += take
                    written += take

                    if self._pos_frame >= self._loop_end_frame:
                        self._pos_frame = self._loop_start_frame
                        if written < n:
                            wrap_seams.append(written)
                else:
                    available = self.total_frames - self._pos_frame
                    if available <= 0:
                        self._playing = False
                        break
                    take = min(n - written, available)
                    out[:, written : written + take] = self.audio[:, self._pos_frame : self._pos_frame + take]
                    self._pos_frame += take
                    written += take
                    if self._pos_frame >= self.total_frames:
                        self._playing = False
                        break

            # Apply declick fades at seam locations
            if declick_frames > 0 and wrap_seams:
                prev_seam = 0
                for idx, seam in enumerate(wrap_seams):
                    next_seam = wrap_seams[idx + 1] if idx + 1 < len(wrap_seams) else n

                    fade_out_len = min(declick_frames, seam - prev_seam)
                    if fade_out_len > 0:
                        ramp_out = np.linspace(1.0, 0.0, fade_out_len + 1, dtype=np.float32)[1:]
                        out[:, seam - fade_out_len : seam] *= ramp_out

                    fade_in_len = min(declick_frames, next_seam - seam)
                    if fade_in_len > 0:
                        ramp_in = np.linspace(0.0, 1.0, fade_in_len + 1, dtype=np.float32)[:-1]
                        out[:, seam : seam + fade_in_len] *= ramp_in

                    prev_seam = seam

            # Apply gain if non-zero
            if self._gain_db != 0.0:
                self.gain_plugin.gain_db = self._gain_db
                out = self.board(out, self.sample_rate, reset=False)

            self._last_write_song_frame = song_frame_at_start
            self._last_write_mono = time.monotonic()
            self._last_write_playing = was_playing
            self._frames_written += n

            return out

    def _feed_loop(self) -> None:
        """Audio feeder loop running in dedicated thread."""
        dev = self.output_device_name
        if dev is None:
            dev = AudioStream.default_output_device_name
        elif isinstance(dev, int) or (isinstance(dev, str) and dev.strip().isdigit()):
            idx = int(dev)
            devs = AudioStream.output_device_names
            if 0 <= idx < len(devs):
                dev = devs[idx]

        try:
            stream_ctx = AudioStream(
                output_device_name=dev,
                num_input_channels=0,
                num_output_channels=self.num_channels,
                sample_rate=self.sample_rate,
                buffer_size=self.buffer_size,
            )
        except Exception:
            # Fallback if specific sample rate is rejected
            stream_ctx = AudioStream(
                output_device_name=dev,
                num_input_channels=0,
                num_output_channels=self.num_channels,
                sample_rate=None,
                buffer_size=self.buffer_size,
            )

        with stream_ctx as stream:
            self.sample_rate = int(stream.sample_rate)
            self._next_write = time.monotonic()

            while not self._quit:
                now = time.monotonic()
                if self._next_write - now > 0.002:
                    time.sleep(self._next_write - now - 0.002)

                block = self._next_block(self.buffer_size)

                try:
                    stream.write(block, self.sample_rate)
                except Exception:
                    break

                now = time.monotonic()
                self._next_write = max(self._next_write + self.buffer_size / float(self.sample_rate), now)
