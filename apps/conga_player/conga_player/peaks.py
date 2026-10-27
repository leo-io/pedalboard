"""Waveform peaks and preview bands computation for conga_player."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
import numpy as np

if TYPE_CHECKING:
    from conga_player.songmap import SongMap


def overview_columns(song_map: SongMap, width: int) -> np.ndarray:
    """Resample the stored 3-band preview to the canvas width using np.interp.

    Returns:
        np.ndarray: Shape (width, 3) float32 with columns [low, mid, high].
    """
    if song_map.preview_bands is None or len(song_map.preview_bands) == 0:
        return np.zeros((width, 3), dtype=np.float32)

    bands = song_map.preview_bands
    num_orig = len(bands)
    if width <= 0:
        return np.zeros((0, 3), dtype=np.float32)
    if width == num_orig:
        return bands.astype(np.float32)

    x_orig = np.linspace(0.0, 1.0, num_orig)
    x_new = np.linspace(0.0, 1.0, width)

    out = np.zeros((width, 3), dtype=np.float32)
    for c in range(3):
        out[:, c] = np.interp(x_new, x_orig, bands[:, c])

    return out


def zoom_peaks(
    audio: np.ndarray,
    sr: int,
    start_s: float,
    end_s: float,
    columns: int,
) -> np.ndarray:
    """Compute mono-mixed max(abs()) per column over [start_s, end_s].

    Args:
        audio: Shape (channels, frames) or (frames,) float32 array.
        sr: Sample rate.
        start_s: Start time in seconds.
        end_s: End time in seconds.
        columns: Number of peak columns to produce.

    Returns:
        np.ndarray: Shape (columns,) float32 array in [0.0, 1.0].
    """
    if columns <= 0:
        return np.zeros(0, dtype=np.float32)

    start_frame = max(0, int(round(start_s * sr)))
    total_frames = audio.shape[-1] if audio.ndim > 0 else 0
    end_frame = min(total_frames, int(round(end_s * sr)))

    if end_frame <= start_frame or total_frames == 0:
        return np.zeros(columns, dtype=np.float32)

    if audio.ndim == 2:
        slice_audio = audio[:, start_frame:end_frame]
        mono = np.max(np.abs(slice_audio), axis=0)
    else:
        mono = np.abs(audio[start_frame:end_frame])

    n_samples = len(mono)
    if n_samples == 0:
        return np.zeros(columns, dtype=np.float32)

    # Split into columns chunks and take max
    chunks = np.array_split(mono, columns)
    peaks = np.zeros(columns, dtype=np.float32)
    for i, chunk in enumerate(chunks):
        if len(chunk) > 0:
            peaks[i] = float(np.max(chunk))

    return peaks


def compute_fallback_preview_bands(
    audio_or_path: Path | str | np.ndarray | None,
    sr: int = 44100,
    columns: int = 1200,
) -> np.ndarray:
    """Generate fallback 3-band preview data (shape (columns, 3) uint8) when JSON is missing."""
    if audio_or_path is None or columns <= 0:
        return np.zeros((columns, 3), dtype=np.uint8)

    audio = None
    if isinstance(audio_or_path, (str, Path)):
        p = Path(audio_or_path)
        if p.exists():
            try:
                from pedalboard.io import AudioFile
                with AudioFile(str(p)) as f:
                    audio = f.read(f.frames)
                    sr = f.samplerate
            except Exception:
                audio = None
    elif isinstance(audio_or_path, np.ndarray):
        audio = audio_or_path

    if audio is None or audio.size == 0:
        return np.zeros((columns, 3), dtype=np.uint8)

    # Simple 3-band energy approximation from audio
    total_frames = audio.shape[-1]
    duration_s = total_frames / float(sr)
    col_peaks = zoom_peaks(audio, sr, 0.0, duration_s, columns)
    # Scale to ~0..50 uint8 typical of rekordbox EX2
    val = np.clip(col_peaks * 50.0, 0, 50).astype(np.uint8)
    bands = np.zeros((columns, 3), dtype=np.uint8)
    bands[:, 0] = (val * 0.7).astype(np.uint8)  # low
    bands[:, 1] = (val * 0.9).astype(np.uint8)  # mid
    bands[:, 2] = val                           # high
    return bands
