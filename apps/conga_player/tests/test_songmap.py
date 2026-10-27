"""Tests for songmap parsing and query helpers."""

from pathlib import Path
import numpy as np
import pytest

from conga_player.songmap import load_song

SONG_FOLDER = Path(__file__).resolve().parent.parent / "songs" / "i_want_you_she_s_so_heavy_eric_kupper"


@pytest.fixture(scope="module")
def song_map():
    assert SONG_FOLDER.is_dir(), f"Song folder missing: {SONG_FOLDER}"
    return load_song(SONG_FOLDER)


def test_song_counts(song_map):
    assert len(song_map.beat_times_s) == 1332
    assert len(song_map.bar_times_s) == 333
    assert len(song_map.phrases) == 47
    assert song_map.bpm == pytest.approx(120.1, abs=1e-3)
    assert song_map.duration_s == pytest.approx(665.71, abs=0.01)


def test_beat_times(song_map):
    assert np.all(np.diff(song_map.beat_times_s) > 0), "beat_times_s must be strictly increasing"
    assert song_map.beat_times_s[0] == pytest.approx(0.358548, abs=1e-5)
    assert song_map.beat_times_s[-1] == pytest.approx(665.294058, abs=1e-5)


def test_phrases(song_map):
    # Every phrase starts on a downbeat
    for p in song_map.phrases:
        beat_idx = p.start_beat - 1
        assert song_map.beat_numbers[beat_idx] == 1, f"Phrase {p.index} does not start on downbeat"

    # Phrase 0 and phrase 46 labels
    assert song_map.phrases[0].label == "Intro"
    assert song_map.phrases[-1].label == "Outro"

    # Spans are contiguous
    for i in range(len(song_map.phrases) - 1):
        assert song_map.phrases[i].end_beat == song_map.phrases[i + 1].start_beat
        assert song_map.phrases[i].end_s == pytest.approx(song_map.phrases[i + 1].start_s, abs=1e-5)


def test_snap_midpoint(song_map):
    # Test snap around a midpoint of two consecutive bars
    b0 = song_map.bar_times_s[10]
    b1 = song_map.bar_times_s[11]
    mid = (b0 + b1) / 2.0

    # Slightly to the left
    assert song_map.snap(mid - 0.05, "bar") == pytest.approx(b0, abs=1e-5)
    # Slightly to the right
    assert song_map.snap(mid + 0.05, "bar") == pytest.approx(b1, abs=1e-5)

    # Test snap off
    assert song_map.snap(12.345, "off") == pytest.approx(12.345, abs=1e-5)

    # Test snap beat
    bt0 = song_map.beat_times_s[20]
    bt1 = song_map.beat_times_s[21]
    bmid = (bt0 + bt1) / 2.0
    assert song_map.snap(bmid - 0.02, "beat") == pytest.approx(bt0, abs=1e-5)
    assert song_map.snap(bmid + 0.02, "beat") == pytest.approx(bt1, abs=1e-5)
