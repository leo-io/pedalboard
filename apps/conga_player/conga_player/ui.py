"""Tkinter user interface for conga_player."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional
import numpy as np

from conga_player.engine import Engine
from conga_player.peaks import overview_columns, zoom_peaks
from conga_player.songmap import Phrase, SongMap

CANVAS_WIDTH = 1200

# Kind -> Hex color mapping for phrases
KIND_COLORS = {
    1: "#3b82f6",  # Intro (Blue)
    2: "#10b981",  # Verse 1 (Emerald)
    3: "#059669",  # Verse 2 (Dark Emerald)
    4: "#14b8a6",  # Verse 3 (Teal)
    5: "#06b6d4",  # Verse 4 (Cyan)
    6: "#6366f1",  # Verse 5 (Indigo)
    7: "#8b5cf6",  # Verse 6 (Violet)
    8: "#f59e0b",  # Bridge (Amber)
    9: "#ef4444",  # Chorus (Red)
    10: "#64748b",  # Outro (Slate)
}
DEFAULT_PHRASE_COLOR = "#a855f7"

ZOOM_LEVELS = [4, 8, 16, 32]
SNAP_MODES = ["beat", "bar", "phrase", "off"]


class CongaPlayerApp:
    def __init__(self, song_map: SongMap, engine: Engine, audio: np.ndarray) -> None:
        self.song_map = song_map
        self.engine = engine
        self.audio = audio
        self.sample_rate = engine.sample_rate

        self.zoom_bars = 8
        self.snap_mode = "beat"

        # Zoom window caching
        self._cached_zoom_bar = -999
        self._cached_zoom_bars_len = self.zoom_bars
        self._zoom_win_start = 0.0
        self._zoom_win_end = 1.0

        # Drag state for setting loops
        self._drag_start_t: Optional[float] = None
        self._dragging_canvas: Optional[str] = None

        # Build window
        self.root = tk.Tk()
        self.root.title(f"conga_player — {song_map.artist} - {song_map.title}")
        self.root.geometry("1240x480")
        self.root.resizable(False, False)
        self.root.configure(bg="#0f172a")

        self._init_styles()
        self._build_ui()
        self._bind_keys()

    def _init_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background="#0f172a", foreground="#f8fafc")
        style.configure("TLabel", background="#0f172a", foreground="#f8fafc", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"), foreground="#38bdf8")
        style.configure("Status.TLabel", font=("Consolas", 11), foreground="#38bdf8", background="#090d16")
        style.configure(
            "TButton",
            font=("Segoe UI", 9, "bold"),
            background="#1e293b",
            foreground="#f8fafc",
            borderwidth=1,
            focuscolor="none",
        )
        style.map(
            "TButton",
            background=[("active", "#334155"), ("pressed", "#475569")],
            foreground=[("active", "#38bdf8")],
        )

    def _build_ui(self) -> None:
        # 1. Header Frame
        header_frame = tk.Frame(self.root, bg="#0f172a", pady=6)
        header_frame.pack(fill=tk.X, padx=20)

        header_text = (
            f"{self.song_map.artist} — {self.song_map.title}    "
            f"Tempo: {self.song_map.bpm:.1f} BPM    Key: {self.song_map.key or 'N/A'}"
        )
        header_lbl = ttk.Label(header_frame, text=header_text, style="Header.TLabel")
        header_lbl.pack(side=tk.LEFT)

        # 2. Main Canvases Container (centered with 20px margins -> 1200px wide)
        canvas_container = tk.Frame(self.root, bg="#0f172a")
        canvas_container.pack(fill=tk.X, padx=20)

        # A. Phrase Lane (h=28)
        self.phrase_canvas = tk.Canvas(
            canvas_container,
            width=CANVAS_WIDTH,
            height=28,
            bg="#090d16",
            highlightthickness=1,
            highlightbackground="#1e293b",
        )
        self.phrase_canvas.pack(pady=(0, 2))
        self._draw_phrase_lane()

        # B. Overview Waveform (h=90)
        self.overview_canvas = tk.Canvas(
            canvas_container,
            width=CANVAS_WIDTH,
            height=90,
            bg="#090d16",
            highlightthickness=1,
            highlightbackground="#1e293b",
        )
        self.overview_canvas.pack(pady=2)
        self._draw_overview_lane()

        # C. Bars Ruler Lane (h=18)
        self.bars_canvas = tk.Canvas(
            canvas_container,
            width=CANVAS_WIDTH,
            height=18,
            bg="#0f172a",
            highlightthickness=0,
        )
        self.bars_canvas.pack(pady=(1, 4))
        self._draw_bars_lane()

        # D. Zoom Lane (h=110)
        self.zoom_canvas = tk.Canvas(
            canvas_container,
            width=CANVAS_WIDTH,
            height=110,
            bg="#090d16",
            highlightthickness=1,
            highlightbackground="#1e293b",
        )
        self.zoom_canvas.pack(pady=(2, 6))

        # 3. Transport Controls Row
        ctrl_frame = tk.Frame(self.root, bg="#0f172a")
        ctrl_frame.pack(fill=tk.X, padx=20, pady=4)

        self.btn_play = ttk.Button(ctrl_frame, text="▶ Play", width=8, command=self._on_toggle_play)
        self.btn_play.pack(side=tk.LEFT, padx=3)

        self.btn_stop = ttk.Button(ctrl_frame, text="■ Stop", width=7, command=self._on_stop)
        self.btn_stop.pack(side=tk.LEFT, padx=3)

        ttk.Separator(ctrl_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)

        self.btn_loop = ttk.Button(ctrl_frame, text="Loop: OFF", width=10, command=self._on_toggle_loop)
        self.btn_loop.pack(side=tk.LEFT, padx=3)

        for bars in (1, 2, 4, 8, 16):
            btn = ttk.Button(
                ctrl_frame,
                text=f"{bars}b",
                width=4,
                command=lambda b=bars: self._on_loop_bars(b),
            )
            btn.pack(side=tk.LEFT, padx=2)

        self.btn_phrase_loop = ttk.Button(ctrl_frame, text="⟲ Phrase", width=9, command=self._on_loop_phrase)
        self.btn_phrase_loop.pack(side=tk.LEFT, padx=4)

        ttk.Separator(ctrl_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)

        self.btn_snap = ttk.Button(
            ctrl_frame,
            text=f"Snap: {self.snap_mode.capitalize()}",
            width=12,
            command=self._on_cycle_snap,
        )
        self.btn_snap.pack(side=tk.LEFT, padx=3)

        ttk.Label(ctrl_frame, text="Zoom:").pack(side=tk.LEFT, padx=(6, 2))
        self.btn_zoom_out = ttk.Button(ctrl_frame, text="-", width=3, command=self._on_zoom_out)
        self.btn_zoom_out.pack(side=tk.LEFT, padx=1)
        self.lbl_zoom = ttk.Label(ctrl_frame, text=f"{self.zoom_bars}b", width=4)
        self.lbl_zoom.pack(side=tk.LEFT, padx=1)
        self.btn_zoom_in = ttk.Button(ctrl_frame, text="+", width=3, command=self._on_zoom_in)
        self.btn_zoom_in.pack(side=tk.LEFT, padx=1)

        # Volume Slider
        vol_frame = tk.Frame(ctrl_frame, bg="#0f172a")
        vol_frame.pack(side=tk.RIGHT, padx=4)
        ttk.Label(vol_frame, text="Vol:").pack(side=tk.LEFT, padx=(0, 4))
        self.vol_slider = ttk.Scale(
            vol_frame,
            from_=-24.0,
            to=6.0,
            value=0.0,
            orient=tk.HORIZONTAL,
            length=100,
            command=self._on_volume_change,
        )
        self.vol_slider.pack(side=tk.LEFT)
        self.lbl_vol = ttk.Label(vol_frame, text="0 dB", width=6)
        self.lbl_vol.pack(side=tk.LEFT, padx=(4, 0))

        # 4. Status Bar Row
        status_frame = tk.Frame(self.root, bg="#090d16", relief=tk.SUNKEN, bd=1)
        status_frame.pack(fill=tk.X, padx=20, pady=(4, 8))

        self.status_lbl = ttk.Label(
            status_frame,
            text="00:00.000   bar 1.1   beat 1   phrase --   loop off",
            style="Status.TLabel",
            padding=(8, 4),
        )
        self.status_lbl.pack(fill=tk.X)

        # Dynamic canvas items
        self.overview_loop_rect = self.overview_canvas.create_rectangle(
            0, 0, 0, 90, fill="#38bdf8", stipple="gray50", outline="#38bdf8", state="hidden"
        )
        self.overview_playhead = self.overview_canvas.create_line(
            0, 0, 0, 90, fill="#ffffff", width=2
        )

        self.zoom_loop_rect = self.zoom_canvas.create_rectangle(
            0, 0, 0, 110, fill="#38bdf8", stipple="gray50", outline="#38bdf8", state="hidden"
        )
        self.zoom_playhead = self.zoom_canvas.create_line(
            0, 0, 0, 110, fill="#ffffff", width=2
        )

        self._bind_mouse_events()

    def _draw_phrase_lane(self) -> None:
        self.phrase_canvas.delete("all")
        dur = self.song_map.duration_s
        if dur <= 0:
            return

        for p in self.song_map.phrases:
            x1 = (p.start_s / dur) * CANVAS_WIDTH
            x2 = (p.end_s / dur) * CANVAS_WIDTH
            color = KIND_COLORS.get(p.kind, DEFAULT_PHRASE_COLOR)

            tag = f"phrase_{p.index}"
            self.phrase_canvas.create_rectangle(
                x1, 0, x2, 28, fill=color, outline="#0f172a", width=1, tags=(tag, "phrase_rect")
            )
            width = x2 - x1
            if width >= 26:
                label_txt = p.label
                if width < 45 and len(label_txt) > 4:
                    label_txt = label_txt[:3]
                self.phrase_canvas.create_text(
                    (x1 + x2) / 2,
                    14,
                    text=label_txt,
                    fill="#ffffff",
                    font=("Segoe UI", 8, "bold"),
                    tags=(tag, "phrase_text"),
                )

    def _draw_overview_lane(self) -> None:
        # Pre-render 3-band waveform lines
        dur = self.song_map.duration_s
        if dur <= 0:
            return

        cols = overview_columns(self.song_map, CANVAS_WIDTH)
        cy = 45.0
        scale = 42.0 / 51.0

        for x in range(CANVAS_WIDTH):
            low = cols[x, 0] * scale
            mid = cols[x, 1] * scale
            high = cols[x, 2] * scale

            # Draw 3-band layers from outer to core
            if high > 1:
                self.overview_canvas.create_line(x, cy - high, x, cy + high, fill="#38bdf8", width=1)
            if mid > 1:
                self.overview_canvas.create_line(x, cy - mid, x, cy + mid, fill="#eab308", width=1)
            if low > 1:
                self.overview_canvas.create_line(x, cy - low, x, cy + low, fill="#2563eb", width=1)

    def _draw_bars_lane(self) -> None:
        self.bars_canvas.delete("all")
        dur = self.song_map.duration_s
        if dur <= 0:
            return

        for i, bar_t in enumerate(self.song_map.bar_times_s):
            x = (bar_t / dur) * CANVAS_WIDTH
            bar_num = i + 1
            if (bar_num - 1) % 16 == 0:
                self.bars_canvas.create_line(x, 0, x, 14, fill="#94a3b8", width=1)
                self.bars_canvas.create_text(
                    x + 2, 7, text=str(bar_num), fill="#94a3b8", font=("Consolas", 8), anchor="w"
                )
            else:
                self.bars_canvas.create_line(x, 0, x, 5, fill="#475569", width=1)

    def _update_zoom_lane(self, cur_pos_s: float, force_redraw: bool = False) -> None:
        cur_bar = self.song_map.bar_index_at(cur_pos_s)
        if not force_redraw and cur_bar == self._cached_zoom_bar and self.zoom_bars == self._cached_zoom_bars_len:
            return

        self._cached_zoom_bar = cur_bar
        self._cached_zoom_bars_len = self.zoom_bars

        # Determine bar window: centered on cur_bar
        half = self.zoom_bars // 2
        total_bars = len(self.song_map.bar_times_s)
        start_bar_idx = max(0, cur_bar - half)
        end_bar_idx = min(total_bars - 1, start_bar_idx + self.zoom_bars)
        if end_bar_idx - start_bar_idx < self.zoom_bars:
            start_bar_idx = max(0, end_bar_idx - self.zoom_bars)

        t_start = float(self.song_map.bar_times_s[start_bar_idx])
        if end_bar_idx + 1 < total_bars:
            t_end = float(self.song_map.bar_times_s[end_bar_idx + 1])
        else:
            t_end = float(self.song_map.duration_s)

        if t_end <= t_start:
            t_end = t_start + 1.0

        self._zoom_win_start = t_start
        self._zoom_win_end = t_end

        # Redraw zoom background items
        self.zoom_canvas.delete("zoom_bg")

        # 1. Peaks
        peaks = zoom_peaks(self.audio, self.sample_rate, t_start, t_end, CANVAS_WIDTH)
        cy = 55.0
        max_h = 48.0
        for x in range(CANVAS_WIDTH):
            h = peaks[x] * max_h
            if h > 0.5:
                self.zoom_canvas.create_line(
                    x, cy - h, x, cy + h, fill="#38bdf8", width=1, tags="zoom_bg"
                )

        # 2. Bar and beat gridlines
        # Find all beats in [t_start, t_end]
        b_first = int(np.searchsorted(self.song_map.beat_times_s, t_start))
        b_last = int(np.searchsorted(self.song_map.beat_times_s, t_end))
        win_dur = t_end - t_start

        for b_idx in range(b_first, min(b_last + 1, len(self.song_map.beat_times_s))):
            bt = self.song_map.beat_times_s[b_idx]
            if bt < t_start or bt > t_end:
                continue
            bx = ((bt - t_start) / win_dur) * CANVAS_WIDTH
            b_num = self.song_map.beat_numbers[b_idx]

            if b_num == 1:
                # Downbeat / Bar line
                bar_idx = self.song_map.bar_index_at(bt)
                self.zoom_canvas.create_line(bx, 0, bx, 110, fill="#64748b", width=1, tags="zoom_bg")
                self.zoom_canvas.create_text(
                    bx + 4,
                    12,
                    text=f"| {bar_idx + 1}",
                    fill="#cbd5e1",
                    font=("Consolas", 9, "bold"),
                    anchor="w",
                    tags="zoom_bg",
                )
            else:
                # Secondary beat ticks
                self.zoom_canvas.create_line(bx, 45, bx, 65, fill="#334155", width=1, tags="zoom_bg")
                self.zoom_canvas.create_text(
                    bx, 12, text="·", fill="#64748b", font=("Consolas", 10), tags="zoom_bg"
                )

        # Ensure loop rect and playhead are on top of background
        self.zoom_canvas.tag_raise(self.zoom_loop_rect)
        self.zoom_canvas.tag_raise(self.zoom_playhead)

    def _bind_mouse_events(self) -> None:
        # Phrase Canvas
        self.phrase_canvas.bind("<Button-1>", self._on_phrase_click)
        self.phrase_canvas.bind("<Double-Button-1>", self._on_phrase_double_click)

        # Overview Canvas
        self.overview_canvas.bind("<ButtonPress-1>", lambda e: self._on_drag_press(e, "overview"))
        self.overview_canvas.bind("<B1-Motion>", lambda e: self._on_drag_motion(e, "overview"))
        self.overview_canvas.bind("<ButtonRelease-1>", lambda e: self._on_drag_release(e, "overview"))

        # Zoom Canvas
        self.zoom_canvas.bind("<ButtonPress-1>", lambda e: self._on_drag_press(e, "zoom"))
        self.zoom_canvas.bind("<B1-Motion>", lambda e: self._on_drag_motion(e, "zoom"))
        self.zoom_canvas.bind("<ButtonRelease-1>", lambda e: self._on_drag_release(e, "zoom"))

    def _bind_keys(self) -> None:
        self.root.bind("<space>", lambda e: self._on_toggle_play())
        self.root.bind("<KeyPress-s>", lambda e: self._on_stop())
        self.root.bind("<KeyPress-S>", lambda e: self._on_stop())
        self.root.bind("<KeyPress-l>", lambda e: self._on_toggle_loop())
        self.root.bind("<KeyPress-L>", lambda e: self._on_toggle_loop())
        self.root.bind("<Escape>", lambda e: self._on_clear_loop())
        self.root.bind("<KeyPress-p>", lambda e: self._on_loop_phrase())
        self.root.bind("<KeyPress-P>", lambda e: self._on_loop_phrase())

        self.root.bind("1", lambda e: self._on_loop_bars(1))
        self.root.bind("2", lambda e: self._on_loop_bars(2))
        self.root.bind("4", lambda e: self._on_loop_bars(4))
        self.root.bind("8", lambda e: self._on_loop_bars(8))

        self.root.bind("<bracketleft>", lambda e: self._on_prev_phrase())
        self.root.bind("<bracketright>", lambda e: self._on_next_phrase())

        self.root.bind("<Left>", lambda e: self._on_seek_step(-1, beat=False))
        self.root.bind("<Right>", lambda e: self._on_seek_step(1, beat=False))
        self.root.bind("<Shift-Left>", lambda e: self._on_seek_step(-1, beat=True))
        self.root.bind("<Shift-Right>", lambda e: self._on_seek_step(1, beat=True))

        self.root.bind("<minus>", lambda e: self._on_zoom_out())
        self.root.bind("<underscore>", lambda e: self._on_zoom_out())
        self.root.bind("<plus>", lambda e: self._on_zoom_in())
        self.root.bind("<equal>", lambda e: self._on_zoom_in())

    # --- Mouse Handlers ---

    def _x_to_time(self, x: float, canvas_name: str) -> float:
        x_clamped = max(0.0, min(float(x), float(CANVAS_WIDTH)))
        if canvas_name == "overview":
            return (x_clamped / CANVAS_WIDTH) * self.song_map.duration_s
        elif canvas_name == "zoom":
            dur = self._zoom_win_end - self._zoom_win_start
            return self._zoom_win_start + (x_clamped / CANVAS_WIDTH) * dur
        return 0.0

    def _on_phrase_click(self, event: tk.Event) -> None:
        dur = self.song_map.duration_s
        t = (max(0, min(event.x, CANVAS_WIDTH)) / CANVAS_WIDTH) * dur
        p = self.song_map.phrase_at(t)
        if p:
            self.engine.seek(p.start_s)

    def _on_phrase_double_click(self, event: tk.Event) -> None:
        dur = self.song_map.duration_s
        t = (max(0, min(event.x, CANVAS_WIDTH)) / CANVAS_WIDTH) * dur
        p = self.song_map.phrase_at(t)
        if p:
            self.engine.set_loop(p.start_s, p.end_s)
            self.engine.set_loop_enabled(True)

    def _on_drag_press(self, event: tk.Event, canvas_name: str) -> None:
        t = self._x_to_time(event.x, canvas_name)
        snapped_t = self.song_map.snap(t, self.snap_mode)
        self._drag_start_t = snapped_t
        self._dragging_canvas = canvas_name

    def _on_drag_motion(self, event: tk.Event, canvas_name: str) -> None:
        if self._drag_start_t is None:
            return
        t = self._x_to_time(event.x, canvas_name)
        snapped_t = self.song_map.snap(t, self.snap_mode)
        if abs(snapped_t - self._drag_start_t) > 0.05:
            # Dynamically display loop region while dragging
            self.engine.set_loop(self._drag_start_t, snapped_t)
            self.engine.set_loop_enabled(True)

    def _on_drag_release(self, event: tk.Event, canvas_name: str) -> None:
        if self._drag_start_t is None:
            return
        t = self._x_to_time(event.x, canvas_name)
        snapped_t = self.song_map.snap(t, self.snap_mode)
        if abs(snapped_t - self._drag_start_t) < 0.05:
            # Single click -> seek
            self.engine.seek(snapped_t)
        else:
            # Drag ended -> establish loop
            self.engine.set_loop(self._drag_start_t, snapped_t)
            self.engine.set_loop_enabled(True)

        self._drag_start_t = None
        self._dragging_canvas = None

    # --- Button & Shortcut Handlers ---

    def _on_toggle_play(self) -> None:
        self.engine.toggle()

    def _on_stop(self) -> None:
        self.engine.stop()

    def _on_toggle_loop(self) -> None:
        st = self.engine.state()
        self.engine.set_loop_enabled(not st.loop_enabled)

    def _on_clear_loop(self) -> None:
        self.engine.clear_loop()

    def _on_loop_bars(self, n_bars: int) -> None:
        st = self.engine.state()
        cur_bar = self.song_map.bar_index_at(st.position_s)
        total_bars = len(self.song_map.bar_times_s)
        start_s = float(self.song_map.bar_times_s[cur_bar])
        end_bar = min(total_bars - 1, cur_bar + n_bars)
        if end_bar + 1 < total_bars:
            end_s = float(self.song_map.bar_times_s[end_bar])
        else:
            end_s = float(self.song_map.duration_s)
        self.engine.set_loop(start_s, end_s)
        self.engine.set_loop_enabled(True)

    def _on_loop_phrase(self) -> None:
        st = self.engine.state()
        p = self.song_map.phrase_at(st.position_s)
        if p:
            self.engine.set_loop(p.start_s, p.end_s)
            self.engine.set_loop_enabled(True)

    def _on_cycle_snap(self) -> None:
        cur_idx = SNAP_MODES.index(self.snap_mode)
        self.snap_mode = SNAP_MODES[(cur_idx + 1) % len(SNAP_MODES)]
        self.btn_snap.config(text=f"Snap: {self.snap_mode.capitalize()}")

    def _on_zoom_in(self) -> None:
        idx = ZOOM_LEVELS.index(self.zoom_bars)
        if idx > 0:
            self.zoom_bars = ZOOM_LEVELS[idx - 1]
            self.lbl_zoom.config(text=f"{self.zoom_bars}b")
            self._update_zoom_lane(self.engine.state().position_s, force_redraw=True)

    def _on_zoom_out(self) -> None:
        idx = ZOOM_LEVELS.index(self.zoom_bars)
        if idx < len(ZOOM_LEVELS) - 1:
            self.zoom_bars = ZOOM_LEVELS[idx + 1]
            self.lbl_zoom.config(text=f"{self.zoom_bars}b")
            self._update_zoom_lane(self.engine.state().position_s, force_redraw=True)

    def _on_volume_change(self, val: str) -> None:
        db = float(val)
        self.lbl_vol.config(text=f"{db:+.1f} dB")
        self.engine.set_gain_db(db)

    def _on_prev_phrase(self) -> None:
        st = self.engine.state()
        p = self.song_map.phrase_at(st.position_s)
        if p:
            if st.position_s > p.start_s + 0.5:
                self.engine.seek(p.start_s)
            elif p.index > 1:
                prev_p = self.song_map.phrases[p.index - 2]
                self.engine.seek(prev_p.start_s)
        elif self.song_map.phrases:
            self.engine.seek(self.song_map.phrases[0].start_s)

    def _on_next_phrase(self) -> None:
        st = self.engine.state()
        p = self.song_map.phrase_at(st.position_s)
        if p and p.index < len(self.song_map.phrases):
            next_p = self.song_map.phrases[p.index]
            self.engine.seek(next_p.start_s)

    def _on_seek_step(self, step: int, beat: bool) -> None:
        st = self.engine.state()
        if beat:
            cur = self.song_map.beat_index_at(st.position_s)
            tgt = max(0, min(cur + step, len(self.song_map.beat_times_s) - 1))
            self.engine.seek(self.song_map.beat_times_s[tgt])
        else:
            cur = self.song_map.bar_index_at(st.position_s)
            tgt = max(0, min(cur + step, len(self.song_map.bar_times_s) - 1))
            self.engine.seek(self.song_map.bar_times_s[tgt])

    # --- Tick Loop ---

    def _tick(self) -> None:
        st = self.engine.state()
        dur = self.song_map.duration_s

        # Update Play/Pause button text
        self.btn_play.config(text="❚❚ Pause" if st.playing else "▶ Play")
        self.btn_loop.config(text="Loop: ON" if st.loop_enabled else "Loop: OFF")

        # 1. Update Overview playhead & loop rect
        if dur > 0:
            px = (st.position_s / dur) * CANVAS_WIDTH
            self.overview_canvas.coords(self.overview_playhead, px, 0, px, 90)

            if st.loop_enabled and st.loop_end_s > st.loop_start_s:
                lx1 = (st.loop_start_s / dur) * CANVAS_WIDTH
                lx2 = (st.loop_end_s / dur) * CANVAS_WIDTH
                self.overview_canvas.coords(self.overview_loop_rect, lx1, 0, lx2, 90)
                self.overview_canvas.itemconfig(self.overview_loop_rect, state="normal")
            else:
                self.overview_canvas.itemconfig(self.overview_loop_rect, state="hidden")

        # 2. Update Zoom lane background & playhead
        self._update_zoom_lane(st.position_s)
        win_dur = self._zoom_win_end - self._zoom_win_start
        if win_dur > 0:
            zpx = ((st.position_s - self._zoom_win_start) / win_dur) * CANVAS_WIDTH
            self.zoom_canvas.coords(self.zoom_playhead, zpx, 0, zpx, 110)

            if st.loop_enabled and st.loop_end_s > st.loop_start_s:
                zlx1 = max(0.0, ((st.loop_start_s - self._zoom_win_start) / win_dur) * CANVAS_WIDTH)
                zlx2 = min(float(CANVAS_WIDTH), ((st.loop_end_s - self._zoom_win_start) / win_dur) * CANVAS_WIDTH)
                if zlx2 > zlx1:
                    self.zoom_canvas.coords(self.zoom_loop_rect, zlx1, 0, zlx2, 110)
                    self.zoom_canvas.itemconfig(self.zoom_loop_rect, state="normal")
                else:
                    self.zoom_canvas.itemconfig(self.zoom_loop_rect, state="hidden")
            else:
                self.zoom_canvas.itemconfig(self.zoom_loop_rect, state="hidden")

        # 3. Update Status text
        mins = int(st.position_s // 60)
        secs = st.position_s % 60
        time_str = f"{mins:02d}:{secs:06.3f}"

        beat_idx = self.song_map.beat_index_at(st.position_s)
        beat_num = self.song_map.beat_numbers[beat_idx] if len(self.song_map.beat_numbers) > 0 else 1
        bar_idx = self.song_map.bar_index_at(st.position_s)
        bar_str = f"bar {bar_idx + 1}.{beat_num}"
        beat_str = f"beat {beat_num}"

        p = self.song_map.phrase_at(st.position_s)
        phrase_str = f"phrase {p.index}/{len(self.song_map.phrases)} {p.label}" if p else "phrase --"

        if st.loop_enabled and st.loop_end_s > st.loop_start_s:
            l_start_bar = self.song_map.bar_index_at(st.loop_start_s) + 1
            l_end_bar = self.song_map.bar_index_at(st.loop_end_s) + 1
            loop_str = f"loop {l_start_bar}–{l_end_bar}"
        else:
            loop_str = "loop off"

        self.status_lbl.config(
            text=f"{time_str}   {bar_str}   {beat_str}   {phrase_str}   {loop_str}"
        )

        self.root.after(33, self._tick)

    def run(self) -> None:
        self._update_zoom_lane(0.0, force_redraw=True)
        self.root.after(33, self._tick)
        self.root.mainloop()
