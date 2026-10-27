"""Main launcher for conga_player."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Ensure this directory is at the front of sys.path
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# Import shadowing check
import pedalboard
pedalboard_path = Path(pedalboard.__file__).resolve()
repo_pedalboard_dir = (APP_DIR.parent.parent / "pedalboard").resolve()
if repo_pedalboard_dir in pedalboard_path.parents or pedalboard_path == repo_pedalboard_dir:
    sys.stderr.write(
        f"ERROR: pedalboard was imported from source tree ({pedalboard_path}) instead of venv.\n"
        f"Please run using apps/conga_player/.venv/Scripts/python main.py\n"
    )
    sys.exit(1)

from pedalboard.io import AudioStream
from conga_player.songmap import load_song


def find_default_song_folder(songs_root: Path) -> Path:
    if not songs_root.exists() or not songs_root.is_dir():
        raise FileNotFoundError(f"Songs directory not found: {songs_root}")

    subdirs = [p for p in songs_root.iterdir() if p.is_dir()]
    if not subdirs:
        raise FileNotFoundError(f"No songs found in {songs_root}")
    if len(subdirs) == 1:
        return subdirs[0]

    # Multiple found without song arg
    options = "\n".join(f"  - {d.name}" for d in subdirs)
    raise ValueError(
        f"Multiple songs found in {songs_root}. Please specify one:\n{options}"
    )


def resolve_song_folder(song_arg: str | None, base_dir: Path) -> Path:
    songs_root = base_dir / "songs"
    if not song_arg:
        return find_default_song_folder(songs_root)

    cand = Path(song_arg)
    if cand.is_dir():
        return cand.resolve()

    # Try relative to songs_root
    in_songs = songs_root / song_arg
    if in_songs.is_dir():
        return in_songs.resolve()

    raise FileNotFoundError(f"Could not find song folder '{song_arg}'")


def resolve_device(device_arg: str | None) -> str | None:
    if not device_arg:
        return None

    devs = AudioStream.output_device_names
    # If passed as numeric index, e.g. "0" or "1"
    if device_arg.strip().isdigit():
        idx = int(device_arg.strip())
        if 0 <= idx < len(devs):
            return devs[idx]
        raise ValueError(f"Device index {idx} out of range (0 to {len(devs) - 1})")

    # Exact match
    if device_arg in devs:
        return device_arg

    # Case-insensitive substring match
    matches = [d for d in devs if device_arg.lower() in d.lower()]
    if matches:
        return matches[0]

    return device_arg


def list_devices() -> None:
    print("Available Output Audio Devices:")
    devs = AudioStream.output_device_names
    default_dev = AudioStream.default_output_device_name
    for i, dev in enumerate(devs):
        is_def = " (Default)" if dev == default_dev else ""
        print(f"  [{i}] {dev}{is_def}")


def run_selftest(song_folder: Path) -> None:
    song_map = load_song(song_folder)
    print(
        f"{len(song_map.beat_times_s)} beats / {len(song_map.bar_times_s)} bars / "
        f"{len(song_map.phrases)} phrases / {song_map.bpm:.1f} BPM / {song_map.duration_s:.2f} s"
    )
    if song_map.phrases:
        print(f"First phrase: {song_map.phrases[0].label}")
        print(f"Last phrase: {song_map.phrases[-1].label}")
    print(f"Audio path: {song_map.audio_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="conga_player — beat/phrase-mapped audio player")
    parser.add_argument("song", nargs="?", default=None, help="Song folder path or slug inside songs/")
    parser.add_argument("--list-devices", action="store_true", help="List audio output devices and exit")
    parser.add_argument("--device", type=str, default=None, help="Output audio device name")
    parser.add_argument("--buffer-size", type=int, default=2048, help="Audio buffer size in frames (default 2048)")
    parser.add_argument("--latency-offset-ms", type=float, default=None, help="Latency offset in ms for playhead position")
    parser.add_argument("--selftest", action="store_true", help="Load song map, print summary, and exit")

    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    song_folder = resolve_song_folder(args.song, APP_DIR)

    if args.selftest:
        run_selftest(song_folder)
        return

    # Load audio into memory
    print(f"Loading song map from {song_folder.name}...")
    song_map = load_song(song_folder)
    print(f"Loading audio from {song_map.audio_path.name}...")

    from pedalboard.io import AudioFile
    with AudioFile(str(song_map.audio_path)) as f:
        audio = f.read(f.frames)
        sr = f.samplerate

    print(f"Preloaded {audio.shape[1]} frames ({audio.shape[1] / sr:.2f} s) at {sr} Hz.")

    device_name = resolve_device(args.device)
    if device_name:
        print(f"Using audio device: {device_name}")

    from conga_player.engine import Engine
    engine = Engine(
        audio=audio,
        sample_rate=sr,
        buffer_size=args.buffer_size,
        output_device_name=device_name,
        latency_offset_ms=args.latency_offset_ms,
    )
    engine.start()

    from conga_player.ui import CongaPlayerApp
    app = CongaPlayerApp(song_map, engine, audio)
    try:
        app.run()
    finally:
        engine.close()


if __name__ == "__main__":
    main()
