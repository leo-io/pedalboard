"""Unit tests for the Audio Engine without requiring an AudioStream device."""

import numpy as np
import pytest

from conga_player.engine import Engine


def test_paused_output_is_all_zeros():
    audio = np.ones((1, 100), dtype=np.float32)
    engine = Engine(audio, sample_rate=1000)
    assert not engine.state().playing

    block = engine._next_block(10)
    assert np.all(block == 0.0)


def test_seek_lands_on_exact_frame():
    audio = np.arange(100, dtype=np.float32).reshape(1, 100)
    engine = Engine(audio, sample_rate=1000, declick_ms=0.0)
    engine.seek(0.042)  # frame 42
    engine.play()

    block = engine._next_block(5)
    assert np.array_equal(block[0], [42, 43, 44, 45, 46])


def test_block_straddling_loop_end():
    # Audio with sequential frames
    audio = np.arange(100, dtype=np.float32).reshape(1, 100)
    engine = Engine(audio, sample_rate=1000, declick_ms=0.0)

    # Loop between frame 20 and 40 (length 20 frames)
    engine.set_loop(0.020, 0.040)
    engine.seek(0.035)  # 5 frames before loop end
    engine.play()

    block = engine._next_block(10)
    expected = np.array([35, 36, 37, 38, 39, 20, 21, 22, 23, 24], dtype=np.float32)
    assert np.array_equal(block[0], expected)


def test_loop_shorter_than_one_block_wraps_twice():
    audio = np.arange(100, dtype=np.float32).reshape(1, 100)
    engine = Engine(audio, sample_rate=1000, declick_ms=0.0)

    # Loop between frame 10 and 14 (length 4 frames)
    engine.set_loop(0.010, 0.014)
    engine.seek(0.010)
    engine.play()

    block = engine._next_block(10)
    expected = np.array([10, 11, 12, 13, 10, 11, 12, 13, 10, 11], dtype=np.float32)
    assert np.array_equal(block[0], expected)


def test_declick_ramps_applied_only_at_seams():
    # Constant 1.0 signal
    audio = np.ones((1, 100), dtype=np.float32)
    # declick_ms=2.0 at 1000 Hz = 2 frames
    engine = Engine(audio, sample_rate=1000, declick_ms=2.0)

    # Loop between frame 10 and 20
    engine.set_loop(0.010, 0.020)
    engine.seek(0.016)  # 4 frames before seam at index 4
    engine.play()

    block = engine._next_block(10)

    # Unseamed frames before fade out: index 0 and 1
    assert block[0, 0] == pytest.approx(1.0)
    assert block[0, 1] == pytest.approx(1.0)

    # Fade out at seam (indices 2, 3)
    assert block[0, 2] < 1.0
    assert block[0, 3] < 1.0

    # Fade in after seam (indices 4, 5)
    assert block[0, 4] < 1.0
    assert block[0, 5] < 1.0

    # Unseamed frames after fade in: indices 6, 7, 8, 9
    assert block[0, 6] == pytest.approx(1.0)
    assert block[0, 7] == pytest.approx(1.0)
    assert block[0, 8] == pytest.approx(1.0)
    assert block[0, 9] == pytest.approx(1.0)


def test_non_looping_playback_stops_at_end():
    audio = np.arange(20, dtype=np.float32).reshape(1, 20)
    engine = Engine(audio, sample_rate=1000, declick_ms=0.0)
    engine.seek(0.015)  # frame 15, only 5 frames left in audio
    engine.play()

    block = engine._next_block(10)
    assert np.array_equal(block[0, :5], [15, 16, 17, 18, 19])
    assert np.all(block[0, 5:] == 0.0)
    assert not engine.state().playing
