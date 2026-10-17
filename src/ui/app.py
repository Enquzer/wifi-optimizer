"""
WiFi Optimizer - Main GUI Application
Redesigned: Optimize button is the hero element.
Shows live step progress, before/after comparison, and improvement score.
"""

import customtkinter as ctk
import threading
import time
import os
import logging
from typing import Optional, List
from PIL import Image

logger = logging.getLogger(__name__)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

COLORS = {
    "bg":           "#0D1117",
    "surface":      "#161B22",
    "surface2":     "#1C2230",
    "border":       "#30363D",
    "accent":       "#00B4FF",
    "accent_glow":  "#0066AA",
    "accent2":      "#7C3AED",
    "success":      "#22C55E",
    "success_dim":  "#14532D",
    "warning":      "#F59E0B",
    "danger":       "#EF4444",
    "text":         "#E6EDF3",
    "text_muted":   "#8B949E",
    "card":         "#21262D",
    "hero_btn":     "#0EA5E9",
    "hero_hover":   "#0284C7",
    "hero_glow":    "#0C4A6E",
}


# ─── Animated Signal Gauge ────────────────────────────────────────────────────
class SignalGauge(ctk.CTkCanvas):
    def __init__(self, master, size=150, label="Signal", **kwargs):
        super().__init__(master, width=size, height=size,
                         bg=COLORS["surface"], highlightthickness=0, **kwargs)
        self.size = size
        self.label = label
        self._signal = 0.0
        self._target = 0.0
        self._quality = ""
        self._after_ids = []
        self.bind("<Destroy>", self._on_destroy)
        self._draw(0.0)

    def _on_destroy(self, _):
        for a in self._after_ids:
            try: self.after_cancel(a)
            except: pass

    def set_signal(self, pct: float, quality: str = ""):
        self._target = max(0.0, min(100.0, float(pct)))
        if quality:
            self._quality = quality
        self._animate()

    def _animate(self):
        diff = self._target - self._signal
        if abs(diff) < 0.8:
            self._signal = self._target
            self._draw(self._signal)
            return
        self._signal += diff * 0.18
        self._draw(self._signal)
        aid = self.after(14, self._animate)
        self._after_ids.append(aid)

    def _color(self, pct):
        if pct >= 75: return COLORS["success"]
        if pct >= 55: return "#86EFAC"
        if pct >= 35: return COLORS["warning"]
        return COLORS["danger"]

    def _draw(self, pct):
        self.delete("all")
        cx = cy = self.size // 2
        r = self.size // 2 - 14
        # background arc
        self._arc(cx, cy, r, 220, 280, COLORS["border"], 10)
        # filled arc
        if pct > 0:
            sweep = (pct / 100.0) * 280
            self._arc(cx, cy, r, 220, sweep, self._color(pct), 10)
        # percentage
        self.create_text(cx, cy - 8, text=f"{int(pct)}%",
                         fill=self._color(pct),
                         font=("Segoe UI", int(self.size * 0.14), "bold"))
        # quality label
        q = self._quality or ""
        self.create_text(cx, cy + 14, text=q,
                         fill=COLORS["text_muted"],
                         font=("Segoe UI", int(self.size * 0.075)))
        self.create_text(cx, cy + 32, text=self.label,
                         fill=COLORS["text_muted"],
                         font=("Segoe UI", int(self.size * 0.068)))

    def _arc(self, cx, cy, r, start, sweep, color, w):
        self.create_arc(cx-r, cy-r, cx+r, cy+r,
                        start=start, extent=-sweep,
                        outline=color, style="arc", width=w)


# ─── Step Item (live progress row) ───────────────────────────────────────────
class StepItem(ctk.CTkFrame):
    STATES = {
        "pending":  ("○", "#4B5563"),
        "running":  ("◉", COLORS["accent"]),
        "done":     ("✔", COLORS["success"]),
        "fail":     ("✘", COLORS["danger"]),
        "skip":     ("⊘", COLORS["text_muted"]),
    }

    def __init__(self, master, text: str, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._icon_lbl = ctk.CTkLabel(self, text="○", width=24,
                                       font=("Segoe UI", 13, "bold"),
                                       text_color="#4B5563")
        self._icon_lbl.pack(side="left")
        self._text_lbl = ctk.CTkLabel(self, text=text,
                                       font=("Segoe UI", 12),
                                       text_color=COLORS["text_muted"],
                                       anchor="w")
        self._text_lbl.pack(side="left", padx=(4, 0))

    def set_state(self, state: str, override_text: str = ""):
        icon, color = self.STATES.get(state, ("○", "#4B5563"))
        self._icon_lbl.configure(text=icon, text_color=color)
        if override_text:
            self._text_lbl.configure(text=override_text,
                                      text_color=COLORS["text"] if state == "done" else color)
        else:
            self._text_lbl.configure(text_color=COLORS["text"] if state == "done" else color)


# ─── Before/After Metric Pair ─────────────────────────────────────────────────
class ImprovementBar(ctk.CTkFrame):
    """Shows a before → after value with delta highlight."""

    def __init__(self, master, label: str, icon: str = "", **kwargs):
        super().__init__(master, fg_color=COLORS["card"],
                         corner_radius=10, border_width=1,
                         border_color=COLORS["border"], **kwargs)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 2))
        ctk.CTkLabel(top, text=f"{icon}  {label}" if icon else label,
                     font=("Segoe UI", 10), text_color=COLORS["text_muted"]).pack(side="left")

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(0, 10))

        self._before_lbl = ctk.CTkLabel(row, text="—",
                                         font=("Segoe UI", 15, "bold"),
                                         text_color=COLORS["text_muted"])
        self._before_lbl.pack(side="left")

        ctk.CTkLabel(row, text="  →  ",
                     font=("Segoe UI", 13),
                     text_color=COLORS["border"]).pack(side="left")

        self._after_lbl = ctk.CTkLabel(row, text="—",
                                        font=("Segoe UI", 15, "bold"),
                                        text_color=COLORS["text_muted"])
        self._after_lbl.pack(side="left")

        self._delta_lbl = ctk.CTkLabel(row, text="",
                                        font=("Segoe UI", 11, "bold"),
                                        text_color=COLORS["success"])
        self._delta_lbl.pack(side="left", padx=(8, 0))

    def set_values(self, before: str, after: str, delta: str = "",
                   improved: bool = True):
        self._before_lbl.configure(text=before, text_color=COLORS["text_muted"])
        after_color = COLORS["success"] if improved else COLORS["warning"]
        self._after_lbl.configure(text=after, text_color=after_color)
        if delta:
            delta_color = COLORS["success"] if improved else COLORS["warning"]
            prefix = "▲ " if improved else "▼ "
            self._delta_lbl.configure(text=prefix + delta, text_color=delta_color)
        else:
            self._delta_lbl.configure(text="")


# ─── Score Badge ──────────────────────────────────────────────────────────────
class ScoreBadge(ctk.CTkCanvas):
    """Circular improvement score badge (0-100)."""

    def __init__(self, master, size=110, **kwargs):
        super().__init__(master, width=size, height=size,
                         bg=COLORS["surface2"], highlightthickness=0, **kwargs)
        self.size = size
        self._score = 0.0
        self._target = 0.0
        self._after_ids = []
        self.bind("<Destroy>", self._cleanup)
        self._draw(0)

    def _cleanup(self, _):
        for a in self._after_ids:
            try: self.after_cancel(a)
            except: pass

    def animate_to(self, score: float):
        self._target = max(0, min(100, score))
        self._run_anim()

    def _run_anim(self):
        diff = self._target - self._score
        if abs(diff) < 0.5:
            self._score = self._target
            self._draw(self._score)
            return
        self._score += diff * 0.12
        self._draw(self._score)
        aid = self.after(14, self._run_anim)
        self._after_ids.append(aid)

    def _draw(self, score):
        self.delete("all")
        cx = cy = self.size // 2
        r = self.size // 2 - 8

        # Glow ring
        if score > 0:
            for gw in range(4, 0, -1):
                self.create_oval(cx - r - gw, cy - r - gw,
                                 cx + r + gw, cy + r + gw,
                                 outline=self._score_color(score),
                                 width=1)

        # Background circle
        self.create_oval(cx - r, cy - r, cx + r, cy + r,
                         fill=COLORS["card"], outline=self._score_color(score), width=2)

        # Score text
        self.create_text(cx, cy - 10,
                         text=f"{int(score)}",
                         fill=self._score_color(score),
                         font=("Segoe UI", int(self.size * 0.22), "bold"))
        self.create_text(cx, cy + 14,
                         text="/ 100",
                         fill=COLORS["text_muted"],
                         font=("Segoe UI", int(self.size * 0.09)))
        self.create_text(cx, cy + 30,
                         text="Score",
                         fill=COLORS["text_muted"],
                         font=("Segoe UI", int(self.size * 0.085)))

    def _score_color(self, s):
        if s >= 75: return COLORS["success"]
        if s >= 55: return "#86EFAC"
        if s >= 40: return COLORS["warning"]
        return COLORS["danger"]


# ─── Metric Chip (small inline metric) ───────────────────────────────────────
class MetricChip(ctk.CTkFrame):
    def __init__(self, master, label: str, value: str = "—",
                 icon: str = "", color: str = COLORS["accent"], **kwargs):
        super().__init__(master, fg_color=COLORS["surface2"],
                         corner_radius=8, border_width=1,
                         border_color=COLORS["border"], **kwargs)
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(padx=10, pady=8)
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack()
        if icon:
            ctk.CTkLabel(top, text=icon, font=("Segoe UI Emoji", 13),
                         text_color=color).pack(side="left")
        ctk.CTkLabel(top, text=f"  {label}", font=("Segoe UI", 9),
                     text_color=COLORS["text_muted"]).pack(side="left")
        self._val = ctk.CTkLabel(inner, text=value,
                                  font=("Segoe UI", 14, "bold"),
                                  text_color=color)
        self._val.pack(pady=(2, 0))

    def update(self, value: str, color: Optional[str] = None):
        kw = {"text": value}
        if color:
            kw["text_color"] = color
        self._val.configure(**kw)


# ─── Network Row ──────────────────────────────────────────────────────────────
class NetworkRow(ctk.CTkFrame):
    def __init__(self, master, ssid, band, channel, signal_pct,
                 score, auth, is_best=False, **kwargs):
        bg = COLORS["surface2"] if not is_best else "#182018"
        super().__init__(master, fg_color=bg, corner_radius=8, **kwargs)
        bc = COLORS["success"] if is_best else COLORS["border"]
        self.configure(border_width=1, border_color=bc)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(padx=12, pady=8, fill="x")
        row.columnconfigure(1, weight=1)

        icon = "📶" if signal_pct >= 60 else ("📡" if signal_pct >= 30 else "⚠️")
        ctk.CTkLabel(row, text=icon, font=("Segoe UI Emoji", 16),
                     width=24).grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 8))

        label = ("⭐ " + ssid if is_best else ssid)[:36]
        ctk.CTkLabel(row, text=label, font=("Segoe UI", 12, "bold"),
                     text_color=COLORS["success"] if is_best else COLORS["text"],
                     anchor="w").grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(row, text=f"{band}  ·  Ch {channel}  ·  {auth}",
                     font=("Segoe UI", 10), text_color=COLORS["text_muted"],
                     anchor="w").grid(row=1, column=1, sticky="w")

        color = self._bc(signal_pct)
        ctk.CTkLabel(row, text=f"{signal_pct}%",
                     font=("Segoe UI", 13, "bold"),
                     text_color=color).grid(row=0, column=2, padx=8, sticky="e")
        ctk.CTkLabel(row, text=f"Score {score:.0f}",
                     font=("Segoe UI", 10),
                     text_color=COLORS["text_muted"]).grid(row=1, column=2, padx=8, sticky="e")

    @staticmethod
    def _bc(p):
        if p >= 80: return COLORS["success"]
        if p >= 60: return "#86EFAC"
        if p >= 40: return COLORS["warning"]
        return COLORS["danger"]


# ─── Main App ─────────────────────────────────────────────────────────────────
class WiFiOptimizerApp(ctk.CTk):
    def __init__(self, assets_dir: str):
        super().__init__()
        self.assets_dir = assets_dir
        self._monitor = None
        self._tray = None
        self._before_snapshot = None   # AdapterInfo before optimization
        self._after_snapshot = None    # AdapterInfo after optimization
        self._all_networks = []
        self._optimizing = False
        self._step_items: List[StepItem] = []

        self._setup_window()
        self._build_ui()
        self._schedule_auto_refresh()

    # ─── Window setup ─────────────────────────────────────────────────────────
    def _setup_window(self):
        self.title("WiFi Optimizer")
        self.geometry("1060x720")
        self.minsize(900, 600)
        self.configure(fg_color=COLORS["bg"])
        self.resizable(True, True)
        ico = os.path.join(self.assets_dir, "app_icon.ico")
        if os.path.exists(ico):
            try: self.iconbitmap(ico)
            except: pass
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─── Layout ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Sidebar
        self._sidebar = ctk.CTkFrame(self, width=200, fg_color=COLORS["surface"],
                                      corner_radius=0)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        ctk.CTkLabel(self._sidebar, text="WiFi\nOptimizer",
                     font=("Segoe UI", 15, "bold"),
                     text_color=COLORS["accent"],
                     justify="center").pack(pady=(22, 12))

        ctk.CTkFrame(self._sidebar, height=1,
                     fg_color=COLORS["border"]).pack(fill="x")

        self._nav_btns = {}
        for key, lbl in [("dashboard", "🏠  Dashboard"),
                           ("networks",  "📡  Networks"),
                           ("settings",  "⚙️   Settings"),
                           ("help",      "❓  How It Works"),
                           ("about",     "ℹ️   About")]:
            b = ctk.CTkButton(self._sidebar, text=lbl, anchor="w",
                               font=("Segoe UI", 12),
                               fg_color="transparent",
                               hover_color=COLORS["surface2"],
                               text_color=COLORS["text"],
                               corner_radius=8, height=40,
                               command=lambda k=key: self._nav(k))
            b.pack(fill="x", padx=8, pady=2)
            self._nav_btns[key] = b

        ctk.CTkFrame(self._sidebar, fg_color="transparent").pack(expand=True)
        self._status_dot = ctk.CTkLabel(self._sidebar, text="● Not scanning",
                                         font=("Segoe UI", 10),
                                         text_color=COLORS["text_muted"])
        self._status_dot.pack(pady=12)

        # Content area
        self._content = ctk.CTkFrame(self, fg_color=COLORS["bg"], corner_radius=0)
        self._content.pack(side="right", fill="both", expand=True)

        self._pages = {}
        self._build_dashboard()
        self._build_networks_page()
        self._build_settings_page()
        self._build_help_page()
        self._build_about_page()
        self._nav("dashboard")

    # ─── Navigation ───────────────────────────────────────────────────────────
    def _nav(self, key):
        for k, f in self._pages.items():
            f.pack_forget()
        self._pages[key].pack(fill="both", expand=True)
        for k, b in self._nav_btns.items():
            if k == key:
                b.configure(fg_color=COLORS["surface2"], text_color=COLORS["accent"])
            else:
                b.configure(fg_color="transparent", text_color=COLORS["text"])

    # ═══════════════════════════════════════════════════════════════════════════
    # DASHBOARD PAGE
    # ═══════════════════════════════════════════════════════════════════════════
    def _build_dashboard(self):
        page = ctk.CTkFrame(self._content, fg_color=COLORS["bg"])
        self._pages["dashboard"] = page

        # ── Scrollable inner ──────────────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(page, fg_color=COLORS["bg"], corner_radius=0)
        scroll.pack(fill="both", expand=True)

        # ─────────────────────────────────────────────────────────────────────
        # HERO: BIG OPTIMIZE BUTTON
        # ─────────────────────────────────────────────────────────────────────
        hero = ctk.CTkFrame(scroll, fg_color=COLORS["surface"],
                             corner_radius=20, border_width=1,
                             border_color=COLORS["accent_glow"])
        hero.pack(fill="x", padx=24, pady=(20, 0))

        # Title row
        htitle = ctk.CTkFrame(hero, fg_color="transparent")
        htitle.pack(fill="x", padx=24, pady=(18, 0))
        # Header action buttons
        h_btns = ctk.CTkFrame(htitle, fg_color="transparent")
        h_btns.pack(side="right")

        self._help_btn = ctk.CTkButton(
            h_btns, text="❓ How It Works", width=125, height=28,
            font=("Segoe UI", 11),
            fg_color=COLORS["surface2"],
            hover_color=COLORS["border"],
            command=lambda: self._nav("help"))
        self._help_btn.pack(side="left", padx=(0, 6))

        self._scan_btn = ctk.CTkButton(
            h_btns, text="🔍 Scan", width=80, height=28,
            font=("Segoe UI", 11),
            fg_color=COLORS["surface2"],
            hover_color=COLORS["border"],
            command=self._do_scan)
        self._scan_btn.pack(side="left")

        # Sub-label
        self._hero_sub = ctk.CTkLabel(
            hero,
            text="Click OPTIMIZE to analyze your connection and apply the best settings.",
            font=("Segoe UI", 12), text_color=COLORS["text_muted"])
        self._hero_sub.pack(padx=24, pady=(4, 0), anchor="w")

        # Profile selector (compact)
        prow = ctk.CTkFrame(hero, fg_color="transparent")
        prow.pack(fill="x", padx=24, pady=(10, 0))
        ctk.CTkLabel(prow, text="Profile:", font=("Segoe UI", 11),
                     text_color=COLORS["text_muted"]).pack(side="left")
        self._profile_var = ctk.StringVar(value="selfish")
        self._profile_seg = ctk.CTkSegmentedButton(
            prow,
            values=["⚡ Selfish Turbo", "Max Speed", "Max Range", "Balanced", "Power Saver"],
            command=self._on_profile_change,
            font=("Segoe UI", 11),
            height=30)
        self._profile_seg.set("⚡ Selfish Turbo")
        self._profile_seg.pack(side="left", padx=(8, 0))

        # ── THE OPTIMIZE BUTTON ───────────────────────────────────────────────
        self._optimize_btn = ctk.CTkButton(
            hero,
            text="⚡  OPTIMIZE NOW",
            font=("Segoe UI", 22, "bold"),
            fg_color=COLORS["hero_btn"],
            hover_color=COLORS["hero_hover"],
            text_color="#FFFFFF",
            corner_radius=16,
            height=72,
            command=self._do_optimize)
        self._optimize_btn.pack(fill="x", padx=24, pady=(14, 0))

        # Progress bar (under optimize button)
        self._progress = ctk.CTkProgressBar(hero, mode="indeterminate",
                                             fg_color=COLORS["surface2"],
                                             progress_color=COLORS["accent"],
                                             height=4, corner_radius=2)
        self._progress_lbl = ctk.CTkLabel(hero, text="",
                                           font=("Segoe UI", 11, "italic"),
                                           text_color=COLORS["accent"])
        self._progress_lbl.pack(padx=24, anchor="w")

        # Spacer below button
        ctk.CTkFrame(hero, fg_color="transparent", height=16).pack()

        # ─────────────────────────────────────────────────────────────────────
        # LIVE STEPS PANEL (hidden until optimizing)
        # ─────────────────────────────────────────────────────────────────────
        self._steps_card = ctk.CTkFrame(scroll, fg_color=COLORS["card"],
                                         corner_radius=14, border_width=1,
                                         border_color=COLORS["border"])
        # Not packed yet — shown when optimization starts

        steps_hdr = ctk.CTkFrame(self._steps_card, fg_color="transparent")
        steps_hdr.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(steps_hdr, text="🔧  Running Optimization",
                     font=("Segoe UI", 13, "bold"),
                     text_color=COLORS["text"]).pack(side="left")
        self._steps_time_lbl = ctk.CTkLabel(steps_hdr, text="",
                                             font=("Segoe UI", 10),
                                             text_color=COLORS["text_muted"])
        self._steps_time_lbl.pack(side="right")

        self._steps_inner = ctk.CTkFrame(self._steps_card, fg_color="transparent")
        self._steps_inner.pack(fill="x", padx=24, pady=(0, 14))

        # ─────────────────────────────────────────────────────────────────────
        # RESULT PANEL (hidden until after first optimization)
        # ─────────────────────────────────────────────────────────────────────
        self._result_card = ctk.CTkFrame(scroll, fg_color=COLORS["card"],
                                          corner_radius=14, border_width=1,
                                          border_color=COLORS["success_dim"])
        # Not packed yet

        # Result header
        rhdr = ctk.CTkFrame(self._result_card, fg_color=COLORS["success_dim"],
                             corner_radius=0)
        rhdr.pack(fill="x")
        rhdr.configure(corner_radius=14)

        rh_inner = ctk.CTkFrame(rhdr, fg_color="transparent")
        rh_inner.pack(fill="x", padx=20, pady=12)

        self._result_title = ctk.CTkLabel(
            rh_inner,
            text="✅  Optimization Complete",
            font=("Segoe UI", 15, "bold"),
            text_color=COLORS["success"])
        self._result_title.pack(side="left")

        self._result_time_lbl = ctk.CTkLabel(rh_inner, text="",
                                              font=("Segoe UI", 11),
                                              text_color=COLORS["text_muted"])
        self._result_time_lbl.pack(side="right")

        # Score + improvement summary row
        rsummary = ctk.CTkFrame(self._result_card, fg_color="transparent")
        rsummary.pack(fill="x", padx=20, pady=(14, 0))

        # Score badge
        score_col = ctk.CTkFrame(rsummary, fg_color="transparent")
        score_col.pack(side="left", padx=(0, 20))
        self._score_badge = ScoreBadge(score_col, size=110)
        self._score_badge.pack()
        ctk.CTkLabel(score_col, text="Connection\nScore",
                     font=("Segoe UI", 9),
                     text_color=COLORS["text_muted"],
                     justify="center").pack(pady=(4, 0))

        # Before/After bars
        ba_grid = ctk.CTkFrame(rsummary, fg_color="transparent")
        ba_grid.pack(side="left", fill="both", expand=True)
        ba_grid.columnconfigure((0, 1), weight=1)

        self._bar_signal = ImprovementBar(ba_grid, "Signal Strength", "📶")
        self._bar_signal.grid(row=0, column=0, padx=(0, 6), pady=4, sticky="ew")

        self._bar_channel = ImprovementBar(ba_grid, "Channel Congestion", "📻")
        self._bar_channel.grid(row=0, column=1, padx=(6, 0), pady=4, sticky="ew")

        self._bar_band = ImprovementBar(ba_grid, "Preferred Band", "📡")
        self._bar_band.grid(row=1, column=0, padx=(0, 6), pady=4, sticky="ew")

        self._bar_speed = ImprovementBar(ba_grid, "Link Speed", "⚡")
        self._bar_speed.grid(row=1, column=1, padx=(6, 0), pady=4, sticky="ew")

        # Settings applied list
        ctk.CTkFrame(self._result_card, height=1,
                     fg_color=COLORS["border"]).pack(fill="x", padx=20, pady=(14, 0))

        applied_hdr = ctk.CTkFrame(self._result_card, fg_color="transparent")
        applied_hdr.pack(fill="x", padx=20, pady=(10, 4))
        ctk.CTkLabel(applied_hdr, text="Settings Applied",
                     font=("Segoe UI", 12, "bold"),
                     text_color=COLORS["text"]).pack(side="left")
        self._applied_count_lbl = ctk.CTkLabel(applied_hdr, text="",
                                                font=("Segoe UI", 11),
                                                text_color=COLORS["success"])
        self._applied_count_lbl.pack(side="right")

        self._applied_frame = ctk.CTkFrame(self._result_card, fg_color="transparent")
        self._applied_frame.pack(fill="x", padx=20, pady=(0, 14))

        # Recommendations
        ctk.CTkFrame(self._result_card, height=1,
                     fg_color=COLORS["border"]).pack(fill="x", padx=20)

        self._rec_frame = ctk.CTkFrame(self._result_card, fg_color="transparent")
        self._rec_frame.pack(fill="x", padx=20, pady=(10, 14))

        # ─────────────────────────────────────────────────────────────────────
        # CURRENT STATUS ROW (gauges + live metrics)
        # ─────────────────────────────────────────────────────────────────────
        status_card = ctk.CTkFrame(scroll, fg_color=COLORS["card"],
                                    corner_radius=14, border_width=1,
                                    border_color=COLORS["border"])
        status_card.pack(fill="x", padx=24, pady=(14, 0))

        ctk.CTkLabel(status_card, text="Live Status",
                     font=("Segoe UI", 12, "bold"),
                     text_color=COLORS["text"]).pack(anchor="w", padx=16, pady=(12, 0))

        status_inner = ctk.CTkFrame(status_card, fg_color="transparent")
        status_inner.pack(fill="x", padx=12, pady=(8, 12))

        # Signal gauge
        g_wrap = ctk.CTkFrame(status_inner, fg_color=COLORS["surface"],
                               corner_radius=12)
        g_wrap.pack(side="left", padx=(4, 12))
        self._gauge = SignalGauge(g_wrap, size=140, label="Signal")
        self._gauge.pack(padx=16, pady=14)

        # Metric chips
        chips_frame = ctk.CTkFrame(status_inner, fg_color="transparent")
        chips_frame.pack(side="left", fill="both", expand=True)
        chips_frame.columnconfigure((0, 1, 2), weight=1)

        self._chips = {}
        chip_defs = [
            ("ssid",    "Network",      "🔗", COLORS["accent"]),
            ("band",    "Band",         "📡", COLORS["accent2"]),
            ("channel", "Channel",      "📻", COLORS["warning"]),
            ("rxrate",  "Link ↓",       "⬇️", COLORS["success"]),
            ("txrate",  "Link ↑",       "⬆️", "#F472B6"),
            ("down",    "Throughput ↓", "🌐", COLORS["success"]),
        ]
        for i, (key, lbl, icon, color) in enumerate(chip_defs):
            chip = MetricChip(chips_frame, label=lbl, icon=icon, color=color)
            chip.grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky="nsew")
            self._chips[key] = chip

        # Bottom spacer
        ctk.CTkFrame(scroll, fg_color="transparent", height=20).pack()

    def _on_profile_change(self, value: str):
        mapping = {
            "⚡ Selfish Turbo": "selfish",
            "Max Speed":       "max_speed",
            "Max Range":       "max_range",
            "Balanced":        "balanced",
            "Power Saver":     "power_save",
        }
        self._profile_var.set(mapping.get(value, "selfish"))

    # ═══════════════════════════════════════════════════════════════════════════
    # NETWORKS PAGE
    # ═══════════════════════════════════════════════════════════════════════════
    def _build_networks_page(self):
        page = ctk.CTkFrame(self._content, fg_color=COLORS["bg"])
        self._pages["networks"] = page

        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(20, 0))
        ctk.CTkLabel(hdr, text="Networks",
                     font=("Segoe UI", 20, "bold"),
                     text_color=COLORS["text"]).pack(side="left")
        ctk.CTkButton(hdr, text="🔄 Refresh", width=90, height=30,
                      font=("Segoe UI", 11),
                      fg_color=COLORS["surface2"],
                      hover_color=COLORS["border"],
                      command=self._do_scan).pack(side="right")

        fbar = ctk.CTkFrame(page, fg_color=COLORS["surface"], corner_radius=10)
        fbar.pack(fill="x", padx=24, pady=(12, 0))
        ctk.CTkLabel(fbar, text="Band:", font=("Segoe UI", 11),
                     text_color=COLORS["text_muted"]).pack(side="left", padx=(12, 6), pady=8)
        self._band_filter = ctk.CTkSegmentedButton(
            fbar, values=["All", "2.4GHz", "5GHz", "6GHz"],
            command=self._apply_filter, font=("Segoe UI", 11))
        self._band_filter.set("All")
        self._band_filter.pack(side="left", pady=8)
        self._net_count_lbl = ctk.CTkLabel(fbar, text="0 networks",
                                            font=("Segoe UI", 11),
                                            text_color=COLORS["text_muted"])
        self._net_count_lbl.pack(side="right", padx=12)

        self._net_scroll = ctk.CTkScrollableFrame(page, fg_color=COLORS["bg"],
                                                   corner_radius=0)
        self._net_scroll.pack(fill="both", expand=True, padx=24, pady=12)
        self._network_rows = []

    # ═══════════════════════════════════════════════════════════════════════════
    # SETTINGS PAGE
    # ═══════════════════════════════════════════════════════════════════════════
    def _build_settings_page(self):
        page = ctk.CTkFrame(self._content, fg_color=COLORS["bg"])
        self._pages["settings"] = page

        ctk.CTkLabel(page, text="Settings",
                     font=("Segoe UI", 20, "bold"),
                     text_color=COLORS["text"]).pack(anchor="w", padx=24, pady=(20, 12))

        scroll = ctk.CTkScrollableFrame(page, fg_color=COLORS["bg"])
        scroll.pack(fill="both", expand=True, padx=24, pady=(0, 20))

        # Adapter name
        ac = self._scard(scroll, "🔌 Wi-Fi Adapter Name",
                          "Usually 'Wi-Fi' — check Device Manager if different.")
        self._adapter_var = ctk.StringVar(value="Wi-Fi")
        ctk.CTkEntry(ac, textvariable=self._adapter_var, width=200,
                     font=("Segoe UI", 12),
                     fg_color=COLORS["surface2"],
                     border_color=COLORS["border"]).pack(anchor="w", padx=8, pady=4)

        # Auto-optimize
        aoc = self._scard(scroll, "🔄 Auto-Optimize on Weak Signal",
                           "Automatically re-runs optimization if signal drops below 30%.")
        self._auto_opt_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(aoc, text="Enable auto-optimization",
                      variable=self._auto_opt_var,
                      font=("Segoe UI", 12), text_color=COLORS["text"],
                      progress_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=4)

        # DNS
        dc = self._scard(scroll, "🌐 DNS Servers",
                          "Fast public DNS reduces latency and speeds up browsing.")
        dr = ctk.CTkFrame(dc, fg_color="transparent")
        dr.pack(anchor="w", padx=8, pady=4)
        ctk.CTkLabel(dr, text="Primary:", font=("Segoe UI", 11),
                     text_color=COLORS["text_muted"]).pack(side="left")
        self._dns1_var = ctk.StringVar(value="1.1.1.1")
        ctk.CTkEntry(dr, textvariable=self._dns1_var, width=120,
                     font=("Segoe UI", 11),
                     fg_color=COLORS["surface2"],
                     border_color=COLORS["border"]).pack(side="left", padx=6)
        ctk.CTkLabel(dr, text="Secondary:", font=("Segoe UI", 11),
                     text_color=COLORS["text_muted"]).pack(side="left")
        self._dns2_var = ctk.StringVar(value="8.8.8.8")
        ctk.CTkEntry(dr, textvariable=self._dns2_var, width=120,
                     font=("Segoe UI", 11),
                     fg_color=COLORS["surface2"],
                     border_color=COLORS["border"]).pack(side="left", padx=6)

        ctk.CTkButton(scroll, text="💾  Save & Optimize",
                      font=("Segoe UI", 13, "bold"),
                      fg_color=COLORS["hero_btn"],
                      hover_color=COLORS["hero_hover"],
                      height=40,
                      command=self._do_optimize).pack(anchor="w", pady=(16, 0))

    def _scard(self, parent, title, description):
        c = ctk.CTkFrame(parent, fg_color=COLORS["card"],
                          corner_radius=12, border_width=1,
                          border_color=COLORS["border"])
        c.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(c, text=title, font=("Segoe UI", 12, "bold"),
                     text_color=COLORS["text"]).pack(anchor="w", padx=16, pady=(12, 2))
        ctk.CTkLabel(c, text=description, font=("Segoe UI", 10),
                     text_color=COLORS["text_muted"]).pack(anchor="w", padx=16, pady=(0, 8))
        return c

    # ═══════════════════════════════════════════════════════════════════════════
    # HOW IT WORKS / HELP PAGE
    # ═══════════════════════════════════════════════════════════════════════════
    def _build_help_page(self):
        page = ctk.CTkFrame(self._content, fg_color=COLORS["bg"])
        self._pages["help"] = page

        # Header
        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(20, 10))
        ctk.CTkLabel(hdr, text="❓  How It Works & Deep-Dive Architecture",
                     font=("Segoe UI", 20, "bold"),
                     text_color=COLORS["text"]).pack(anchor="w")
        ctk.CTkLabel(hdr, text="Complete mathematical specifications, RF physics formulas, kernel tweaks, and protocol diagrams.",
                     font=("Segoe UI", 11),
                     text_color=COLORS["text_muted"]).pack(anchor="w", pady=(2, 0))

        scroll = ctk.CTkScrollableFrame(page, fg_color=COLORS["bg"], corner_radius=0)
        scroll.pack(fill="both", expand=True, padx=24, pady=(0, 20))

        def _help_card(title: str, icon: str, subtitle: str, color=COLORS["accent"]) -> ctk.CTkFrame:
            card = ctk.CTkFrame(scroll, fg_color=COLORS["card"], corner_radius=12,
                                border_width=1, border_color=COLORS["border"])
            card.pack(fill="x", pady=(0, 14))

            chdr = ctk.CTkFrame(card, fg_color="transparent")
            chdr.pack(fill="x", padx=16, pady=(12, 4))
            ctk.CTkLabel(chdr, text=f"{icon}  {title}", font=("Segoe UI", 13, "bold"),
                         text_color=color).pack(side="left")

            ctk.CTkLabel(card, text=subtitle, font=("Segoe UI", 10),
                         text_color=COLORS["text_muted"]).pack(anchor="w", padx=16, pady=(0, 8))

            content_box = ctk.CTkFrame(card, fg_color="transparent")
            content_box.pack(fill="x", padx=16, pady=(0, 12))
            return content_box

        def _code_box(parent, text: str):
            c_frame = ctk.CTkFrame(parent, fg_color=COLORS["surface2"], corner_radius=8,
                                   border_width=1, border_color=COLORS["border"])
            c_frame.pack(fill="x", pady=4, padx=4)
            ctk.CTkLabel(c_frame, text=text, font=("Consolas", 9),
                         text_color=COLORS["text"], justify="left", anchor="w").pack(padx=12, pady=8, fill="x")

        def _formula_box(parent, title: str, formula: str):
            f_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg"], corner_radius=6,
                                   border_width=1, border_color=COLORS["border"])
            f_frame.pack(fill="x", pady=3, padx=4)
            top = ctk.CTkFrame(f_frame, fg_color="transparent")
            top.pack(fill="x", padx=10, pady=(6, 2))
            ctk.CTkLabel(top, text=title, font=("Segoe UI", 10, "bold"),
                         text_color=COLORS["text_muted"]).pack(side="left")
            ctk.CTkLabel(f_frame, text=formula, font=("Consolas", 11, "bold"),
                         text_color=COLORS["accent"], justify="left", anchor="w").pack(padx=12, pady=(0, 6), fill="x")

        # ── 0. End-to-End System Architecture Diagram ────────────────────────
        c0 = _help_card(
            "System Architecture: 5-Stage Cross-Layer Optimization Pipeline",
            "🗺️",
            "How physical radio telemetry, MAC contention, driver NDIS, and kernel TCP/IP connect.",
            color=COLORS["accent"]
        )
        diagram_text = (
            "┌────────────────────────────────────────────────────────────────────────┐\n"
            "│                      1. RF & PHY TELEMETRY ENGINE                      │\n"
            "│ • Raw Beacon & Interface Extraction (BSSID, RSSI, Frequency, Radio)   │\n"
            "│ • Reference Path Loss at 1m: PL(1m) = 20*log10(f_MHz) - 27.55          │\n"
            "│ • Inverted Log-Distance: d = 10 ^ ((P_tx - P_rx - PL(1m)) / (10*n))     │\n"
            "│ • Spectral Overlap Matrix w_ov (2.4GHz Δch: 1.0, 0.75, 0.5, 0.25, 0.0)  │\n"
            "└───────────────────────────────────┬────────────────────────────────────┘\n"
            "                                    │\n"
            "                                    ▼\n"
            "┌────────────────────────────────────────────────────────────────────────┐\n"
            "│            2. MULTI-OBJECTIVE BSSID FITNESS FUNCTION (M-BFF)           │\n"
            "│   S(N) = f_sig(P_rx) + f_cong(C) + f_band(B, P_rx) +                    │\n"
            "│          f_phy(R) + f_sec(A)    ∈ [0, 100]                             │\n"
            "│ • Calibrated Sigmoid: f_sig = 30 / (1 + exp(-0.35 * (P_rx + 72)))      │\n"
            "│ • AP Roaming Guard: +10 pts Hysteresis Margin & 30s Cooldown           │\n"
            "└───────────────────────────────────┬────────────────────────────────────┘\n"
            "                                    │\n"
            "                                    ▼\n"
            "┌────────────────────────────────────────────────────────────────────────┐\n"
            "│         3. TIER A: DISCOVERY-VALIDATED HARDWARE NDIS DRIVER TUNING     │\n"
            "│ • Parameter Discovery: Inspects Ndi\\params before setting properties    │\n"
            "│ • *TransmitPower = 5 (100% ERP Uplink EIRP Maximization)               │\n"
            "│ • *RoamAggressiveness = 5 (Rapid Handoff, Eliminates Sticky Client)    │\n"
            "│ • *PreferredBand = 2 (5GHz) / 1 (2.4GHz) / 0 (Auto Steering)           │\n"
            "│ • High-Performance Wireless AC Power Scheme Enforcement               │\n"
            "└───────────────────────────────────┬────────────────────────────────────┘\n"
            "                                    │\n"
            "                                    ▼\n"
            "┌────────────────────────────────────────────────────────────────────────┐\n"
            "│         4. TIER B: KERNEL TRANSPORT LAYER (TCP/IP) OPTIMIZATION        │\n"
            "│ • TCP Receive Window Auto-Tuning = Normal (BDP Scaling up to 16MB)     │\n"
            "│ • Receive Side Scaling (RSS) = Distributes IRQs across CPU Cores       │\n"
            "│ • Explicit Congestion Notification (ECN) = Prevents Bufferbloat Drops  │\n"
            "│ • Optional Low-Jitter Packet Coalescing Optimization                   │\n"
            "└───────────────────────────────────┬────────────────────────────────────┘\n"
            "                                    │\n"
            "                                    ▼\n"
            "┌────────────────────────────────────────────────────────────────────────┐\n"
            "│                  5. CLOSED-LOOP LINK GOVERNOR                          │\n"
            "│ • EWMA Telemetry Smoothing: P_tilde(t) = 0.3*P(t) + 0.7*P_tilde(t-1)   │\n"
            "│ • Two-State Machine: Healthy <--> Degraded (3-sample fall / 5 recover) │\n"
            "│ • Non-Destructive: No driver reloads or interface resets mid-session   │\n"
            "└────────────────────────────────────────────────────────────────────────┘"
        )
        _code_box(c0, diagram_text)

        # ── 1. The Adaptive Radio Optimization Strategy ───────────────────────
        c1 = _help_card(
            "1. The Adaptive Radio & Protocol Optimization Strategy",
            "⚡",
            "How client adapter settings and QoS tags optimize throughput and channel utilization.",
            color=COLORS["hero_btn"]
        )
        points_c1 = [
            ("Discovery-Based Parameter Validation",
             "Instead of blindly forcing registry keys, the optimizer queries the Windows NDIS 'Ndi\\params' database to ensure only properties officially supported by your adapter's OEM driver are configured."),
            ("802.1p QoS / Priority Tagging (*PriorityVLANTag)",
             "Tags outgoing packets for appropriate traffic classes so local network queues handle latency-sensitive streams without delay."),
            ("Maximum Transmit Power & Zero-Sleep Transceiver (*TransmitPower = 5)",
             "Ensures the client transceiver operates at 100% allowable EIRP, resolving uplink asymmetric budget bottlenecks when communicating with high-gain routers."),
            ("Standards Note on Airtime & Contention",
             "Under IEEE 802.11 standards, channel contention parameters (CWmin, CWmax, TXOP) are broadcast by the Access Point. Client-side tuning optimizes local transmission queues and adapter sensitivity without violating router standards."),
        ]
        for title, desc in points_c1:
            row = ctk.CTkFrame(c1, fg_color=COLORS["surface2"], corner_radius=8)
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=f"• {title}", font=("Segoe UI", 11, "bold"),
                         text_color=COLORS["text"], anchor="w").pack(padx=10, pady=(6, 2), fill="x")
            ctk.CTkLabel(row, text=desc, font=("Segoe UI", 10),
                         text_color=COLORS["text_muted"], anchor="w", justify="left",
                         wraplength=760).pack(padx=10, pady=(0, 6), fill="x")

        # ── 2. Physics & Distance Modeling ────────────────────────────────────
        c2 = _help_card(
            "2. RF Radio Physics & The Log-Distance Propagation Model",
            "📡",
            "How the algorithm evaluates attenuation, wavelength, and calculates your distance to the router.",
            color=COLORS["accent"]
        )
        _formula_box(c2, "Receiver Power Conversion from Windows Telemetry",
                     "P_rx (dBm) = (Signal_Percent / 2.0) - 100.0   [e.g., 50% -> -75 dBm]")
        _formula_box(c2, "1-Meter Reference Path Loss & Log-Distance Distance Formula",
                     "PL(1m) = 20*log10(f_MHz) - 27.55   [5180 MHz -> 46.74 dB | 2437 MHz -> 40.19 dB]\n"
                     "d (meters) = 10 ^ ((P_tx - P_rx - 20*log10(f_MHz) + 27.55) / (10 * n))")

        distance_table = (
            "Frequency Band │ Frequency (MHz) │ Wavelength (λ) │ Obstacle Penetration │ Optimal Regime\n"
            "───────────────┼─────────────────┼────────────────┼──────────────────────┼─────────────────────────\n"
            "2.4 GHz WiFi   │ 2412 - 2484 MHz │ ~12.5 cm       │ High (Concrete/Walls)│ Far distance (>12m)\n"
            "5.0 GHz WiFi   │ 5180 - 5825 MHz │ ~5.5 cm        │ Medium (Attenuates)  │ Near router (<10m, high QAM)\n"
            "6.0 GHz WiFi 6E│ 5925 - 7125 MHz │ ~4.5 cm        │ Low (Line-of-Sight)  │ Ultra-wide 160MHz channels"
        )
        _code_box(c2, distance_table)

        # ── 3. Multi-Objective Scoring Algorithm ──────────────────────────────
        c3 = _help_card(
            "3. The Multi-Objective BSSID Fitness Function (M-BFF)",
            "📊",
            "How every detected WiFi access point is mathematically evaluated and scored (0–100 Points).",
            color=COLORS["warning"]
        )
        _formula_box(c3, "Calibrated Continuous Sigmoid Signal Curve (0-30 pts)",
                     "f_sig(P_rx) = 30 / (1 + exp(-0.35 * (P_rx + 72.0)))   [Inflection: -72 dBm = 15.0 pts]")
        _formula_box(c3, "Effective Channel Congestion Function (0-20 pts)",
                     "f_cong(C) = max(0.0, 20.0 - 5.0 * C_eff)\n"
                     "C_eff = Σ w_ov(ch_i, ch_j) * w_sig(P_j)   [w_ov: 1.0, 0.75, 0.50, 0.25, 0.0]")

        scoring_items = [
            ("Signal Strength (0-30 pts)", "Calibrated Sigmoid: links > -55 dBm reach 30 pts; drops sharply below -75 dBm where Bit Error Rate (BER) spikes; down to 0.05 pts at -90 dBm."),
            ("Spectral Congestion (0-20 pts)", "Penalizes co-channel and adjacent-channel interference (CCI) weighted by neighbor signal levels."),
            ("Band Capacity (0-20 pts)", "Evaluates 5GHz/6GHz bandwidth capacity modulated by link margin distance attenuation h(P_rx)."),
            ("PHY Modulation (0-20 pts)", "WiFi 7 (802.11be) = 20 pts > WiFi 6 (802.11ax) = 18 pts > WiFi 5 (802.11ac) = 14 pts > WiFi 4 = 9 pts."),
            ("Security Cipher (0-10 pts)", "WPA3 = 10 pts, WPA2-AES = 8 pts, WPA2-TKIP = 3 pts, Open/WEP = 0 pts (flagged as insecure)."),
        ]
        for name, detail in scoring_items:
            r = ctk.CTkFrame(c3, fg_color="transparent")
            r.pack(fill="x", pady=2)
            ctk.CTkLabel(r, text=f"✔  {name}:", font=("Segoe UI", 10, "bold"),
                         text_color=COLORS["accent"], width=200, anchor="w").pack(side="left")
            ctk.CTkLabel(r, text=detail, font=("Segoe UI", 10),
                         text_color=COLORS["text"], anchor="w").pack(side="left")

        # ── 4. Dynamic Governor & Kernel Stack ────────────────────────────────
        c4 = _help_card(
            "4. Dynamic Feedback Governor & Windows Kernel (TCP/IP) Optimization",
            "🌐",
            "Unlocking the operating system network pipe to prevent software-level throttling.",
            color=COLORS["success"]
        )
        _formula_box(c4, "EWMA-Smoothed Telemetry & Anti-Flapping Governor",
                     "P_tilde(t) = 0.3 * P_rx(t) + 0.7 * P_tilde(t-1)   [Smoothed RSSI]\n"
                     "Healthy -> Degraded: 3 consecutive samples < -75 dBm | Degraded -> Healthy: 5 samples >= -70 dBm")

        kernel_items = [
            ("TCP Receive Window Auto-Tuning (Normal)", "Scales receive buffers dynamically up to 16MB based on the Bandwidth-Delay Product (BDP = Bandwidth * RTT)."),
            ("Receive Side Scaling (RSS)", "Distributes network packet interrupt servicing across physical CPU cores to eliminate Core 0 bottlenecks."),
            ("Explicit Congestion Notification (ECN)", "Instructs routers to mark congested packets rather than dropping them, ending bufferbloat latency spikes."),
            ("DNS Flush & Fast Anycast Resolvers", "Flushes stale DNS records and configures Cloudflare (1.1.1.1) and Google (8.8.8.8) sub-10ms resolvers."),
        ]
        for name, detail in kernel_items:
            r = ctk.CTkFrame(c4, fg_color="transparent")
            r.pack(fill="x", pady=2)
            ctk.CTkLabel(r, text=f"• {name}:", font=("Segoe UI", 10, "bold"),
                         text_color=COLORS["success"], width=240, anchor="w").pack(side="left")
            ctk.CTkLabel(r, text=detail, font=("Segoe UI", 10),
                         text_color=COLORS["text"], anchor="w").pack(side="left")

        # ── 5. Safety, Privacy & Reversibility ────────────────────────────────
        c5 = _help_card(
            "5. Safety, Privacy & Atomic Snapshot Rollback",
            "🛡️",
            "Why administrator rights are requested and how settings are safely managed and reversed.",
            color="#A78BFA"
        )
        ctk.CTkLabel(c5, text="• Atomic Rollback Snapshots: Every modified adapter registry key is saved before changes are applied (adapter_rollback_snapshot.json). Original values are restored and created keys are deleted on rollback.\n• Parameter Discovery: Only driver properties verified via 'Ndi\\params' are modified, preventing invalid writes.\n• 100% Non-Destructive: Background monitoring never restarts the network adapter or disrupts active streams.\n• Why Administrator is Needed: Windows protects adapter hardware registry keys from unprivileged access. Running as Administrator unlocks direct hardware transceiver tuning.",
                     font=("Segoe UI", 10), text_color=COLORS["text"],
                     justify="left", anchor="w", wraplength=760).pack(padx=8, pady=4, fill="x")

    # ═══════════════════════════════════════════════════════════════════════════
    # ABOUT PAGE
    # ═══════════════════════════════════════════════════════════════════════════
    def _build_about_page(self):
        page = ctk.CTkFrame(self._content, fg_color=COLORS["bg"])
        self._pages["about"] = page
        ctk.CTkFrame(page, fg_color="transparent").pack(expand=True)
        ctk.CTkLabel(page, text="📶", font=("Segoe UI Emoji", 64)).pack()
        ctk.CTkLabel(page, text="WiFi Optimizer",
                     font=("Segoe UI", 28, "bold"),
                     text_color=COLORS["accent"]).pack(pady=(6, 2))
        ctk.CTkLabel(page, text="v1.0.0  —  Windows Edition",
                     font=("Segoe UI", 12),
                     text_color=COLORS["text_muted"]).pack()

        # Credits Box
        credit_box = ctk.CTkFrame(page, fg_color=COLORS["surface2"], corner_radius=12,
                                  border_width=1, border_color=COLORS["accent_glow"])
        credit_box.pack(pady=(16, 0), padx=80)
        ctk.CTkLabel(credit_box, text="⚡ Powered by — Fashion Flow 2026",
                     font=("Segoe UI", 13, "bold"),
                     text_color=COLORS["accent"]).pack(padx=28, pady=(10, 3))
        ctk.CTkLabel(credit_box, text="Developed by Enquzer Getachew",
                     font=("Segoe UI", 12, "bold"),
                     text_color=COLORS["text"]).pack(padx=28, pady=(0, 10))

        ctk.CTkFrame(page, height=1, fg_color=COLORS["border"]).pack(
            fill="x", padx=80, pady=20)
        for f in ["🔍  Scans all visible WiFi networks with full scoring",
                  "📊  Rates each network: signal · channel · band · security · speed",
                  "⚙️   Applies adapter settings via Windows registry & netsh",
                  "📈  Shows before vs after improvement for every run",
                  "🔄  Background monitor with auto-optimize on weak signal",
                  "🔔  Windows 10/11 toast notifications",
                  "🖥️   System tray icon with live signal level indicator",
                  "⚡  TCP/IP stack optimization for lower latency"]:
            ctk.CTkLabel(page, text=f, font=("Segoe UI", 11),
                         text_color=COLORS["text"]).pack(anchor="w", padx=120, pady=2)
        ctk.CTkFrame(page, fg_color="transparent").pack(expand=True)

    # ═══════════════════════════════════════════════════════════════════════════
    # ACTIONS
    # ═══════════════════════════════════════════════════════════════════════════
    def _do_scan(self):
        self._scan_btn.configure(state="disabled", text="Scanning…")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        from core.wifi_scanner import scan_networks, get_connected_network
        from core.optimizer import score_network, PROFILES, get_channel_congestion
        try:
            nets = scan_networks()
            adapter = get_connected_network()
            profile = PROFILES.get(self._profile_var.get(), PROFILES["balanced"])
            cong = get_channel_congestion(nets)
            scored = sorted([score_network(n, cong, profile) for n in nets],
                            key=lambda x: x.total_score, reverse=True)
            if adapter:
                for sc in scored:
                    if sc.network.ssid == adapter.ssid:
                        sc.network.connected = True
            self._all_networks = scored
            self.after(0, lambda: (
                self._update_chips(adapter),
                self._update_gauge(adapter),
                self._update_networks_page(scored),
            ))
        except Exception as e:
            logger.error("Scan error: %s", e)
        finally:
            self.after(0, lambda: self._scan_btn.configure(
                state="normal", text="🔍 Scan"))

    def _do_optimize(self):
        if self._optimizing:
            return
        self._optimizing = True
        self._opt_start_time = time.time()

        # Capture BEFORE state
        from core.wifi_scanner import get_connected_network
        self._before_snapshot = get_connected_network()

        # Change to dashboard
        self._nav("dashboard")

        # Lock button, show progress
        self._optimize_btn.configure(
            state="disabled",
            text="⏳  Optimizing…",
            fg_color=COLORS["accent_glow"])
        self._progress.pack(fill="x", padx=24, pady=(4, 0))
        self._progress.start()
        self._hero_sub.configure(
            text="Analyzing your connection and applying the best settings…",
            text_color=COLORS["accent"])

        # Show steps card and build step rows
        self._steps_card.pack(fill="x", padx=24, pady=(14, 0))
        for w in self._steps_inner.winfo_children():
            w.destroy()
        self._step_items.clear()

        step_defs = [
            "Scanning visible networks",
            "Analyzing signal strength & channel congestion",
            "Scoring networks (signal · band · channel · security)",
            "Setting preferred band",
            "Setting roaming aggressiveness",
            "Maximizing transmit power",
            "Disabling power saving mode",
            "Enabling background scanning",
            "Optimizing TCP/IP stack",
            "Setting fast DNS servers",
            "Flushing DNS cache",
            "Verifying connection",
        ]
        for txt in step_defs:
            item = StepItem(self._steps_inner, txt)
            item.pack(fill="x", pady=2)
            self._step_items.append(item)

        # Notify
        try:
            from ui.notifications import notify_optimization_start
            notify_optimization_start(self._profile_var.get())
        except: pass

        if self._tray:
            try: self._tray.set_optimizing()
            except: pass

        threading.Thread(target=self._optimize_worker, daemon=True).start()

    def _optimize_worker(self):
        from core.optimizer import run_optimization
        step_idx = [0]

        def _step(msg: str, state="running"):
            idx = step_idx[0]
            if idx < len(self._step_items):
                self.after(0, lambda i=idx, s=state, m=msg:
                           self._update_step(i, s, m))
            # Update progress label
            self.after(0, lambda m=msg: self._progress_lbl.configure(text=m))

        def _advance(msg: str = ""):
            idx = step_idx[0]
            if idx < len(self._step_items):
                self.after(0, lambda i=idx: self._update_step(i, "done"))
            step_idx[0] += 1
            if step_idx[0] < len(self._step_items):
                self.after(0, lambda i=step_idx[0]: self._update_step(i, "running"))
            if msg:
                self.after(0, lambda m=msg: self._progress_lbl.configure(text=m))

        def progress(msg: str):
            _step(msg, "running")
            # Map message to step
            lower = msg.lower()
            if "scanning" in lower and "netw" in lower:
                pass  # step 0 already active
            elif "found" in lower or "analyzing" in lower or "congestion" in lower:
                _advance(msg)
            elif "scoring" in lower or "scor" in lower:
                _advance(msg)
            elif "band" in lower:
                _advance(msg)
            elif "roaming" in lower:
                _advance(msg)
            elif "power" in lower and "transmit" in lower:
                _advance(msg)
            elif "power" in lower:
                _advance(msg)
            elif "background" in lower:
                _advance(msg)
            elif "tcp" in lower:
                _advance(msg)
            elif "dns" in lower and "setting" in lower.replace("settings", ""):
                _advance(msg)
            elif "flush" in lower:
                _advance(msg)
            elif "verif" in lower or "complete" in lower:
                _advance(msg)

        # Mark step 0 running
        self.after(0, lambda: self._update_step(0, "running"))

        try:
            result = run_optimization(
                profile_key=self._profile_var.get(),
                adapter_name=getattr(self, '_adapter_var',
                                     type('_', (), {'get': lambda s: 'Wi-Fi'})()).get(),
                progress_callback=progress,
            )
            # Mark remaining steps done
            for i in range(len(self._step_items)):
                self.after(0, lambda idx=i, r=result: self._update_step(
                    idx,
                    "done" if idx < len(r.settings_applied) + 4 else "skip"))

            # Capture AFTER
            from core.wifi_scanner import get_connected_network
            self._after_snapshot = get_connected_network()
            elapsed = time.time() - self._opt_start_time

            self.after(300, lambda: self._show_result(result, elapsed))
        except Exception as e:
            logger.error("Optimization error: %s", e)
            self.after(0, lambda: self._progress_lbl.configure(
                text=f"Error: {e}", text_color=COLORS["danger"]))
            for i, item in enumerate(self._step_items):
                self.after(0, lambda idx=i: self._update_step(idx, "fail"))
        finally:
            self.after(0, self._optimize_done)

    def _update_step(self, idx: int, state: str, text_override: str = ""):
        if 0 <= idx < len(self._step_items):
            self._step_items[idx].set_state(state, text_override)

    def _optimize_done(self):
        self._optimizing = False
        self._optimize_btn.configure(
            state="normal",
            text="⚡  OPTIMIZE NOW",
            fg_color=COLORS["hero_btn"])
        self._progress.stop()
        self._progress.pack_forget()
        self._hero_sub.configure(
            text="Optimization complete! See your results below.",
            text_color=COLORS["success"])

    def _show_result(self, result, elapsed: float):
        from ui.notifications import notify_optimization_complete

        # ── Compute improvement score ─────────────────────────────────────────
        before = self._before_snapshot
        after = self._after_snapshot or before

        # Score = avg of (settings applied ratio) + (network score)
        applied_ratio = min(1.0, len(result.settings_applied) / 10.0)
        net_score = result.network_scores[0].total_score if result.network_scores else 50.0
        total_score = int((applied_ratio * 50) + (net_score * 0.5))

        self._score_badge.animate_to(total_score)

        # ── Before / After bars ───────────────────────────────────────────────
        if before:
            b_sig = before.signal_percent
            a_sig = after.signal_percent if after else b_sig
            sig_delta = a_sig - b_sig
            self._bar_signal.set_values(
                before=f"{b_sig}%",
                after=f"{a_sig}%",
                delta=f"{abs(sig_delta)}%" if sig_delta != 0 else "Stable",
                improved=sig_delta >= 0)

            b_ch = f"Ch {before.channel}" if before.channel else "?"
            a_ch = f"Ch {after.channel}" if (after and after.channel) else b_ch
            self._bar_channel.set_values(before=b_ch, after=a_ch,
                                          delta="Optimized" if b_ch != a_ch else "Stable",
                                          improved=True)

            b_band = before.radio_type or "2.4GHz"
            a_band = after.radio_type if after else b_band
            self._bar_band.set_values(before=b_band, after=a_band,
                                       delta="Switched" if b_band != a_band else "Maintained",
                                       improved=True)

            b_spd = f"{before.receive_rate_mbps:.0f} Mbps" if before.receive_rate_mbps else "?"
            a_spd = f"{after.receive_rate_mbps:.0f} Mbps" if (after and after.receive_rate_mbps) else b_spd
            spd_delta = (after.receive_rate_mbps - before.receive_rate_mbps) if after else 0
            self._bar_speed.set_values(before=b_spd, after=a_spd,
                                        delta=f"{abs(spd_delta):.0f} Mbps" if spd_delta else "",
                                        improved=spd_delta >= 0)
        else:
            self._bar_signal.set_values("—", f"{result.adapter_info.signal_percent}%" if result.adapter_info else "—",
                                         improved=True)
            self._bar_channel.set_values("—", "Applied", improved=True)
            self._bar_band.set_values("—", result.network_scores[0].network.band
                                       if result.network_scores else "—", improved=True)
            self._bar_speed.set_values("—", "—")

        # ── Applied settings chips ────────────────────────────────────────────
        for w in self._applied_frame.winfo_children():
            w.destroy()

        count = len(result.settings_applied)
        self._applied_count_lbl.configure(
            text=f"{count} of {count + len(result.settings_failed)} applied",
            text_color=COLORS["success"] if count >= 5 else COLORS["warning"])

        cols = 2
        for i, s in enumerate(result.settings_applied):
            row_frame = self._applied_frame
            chip = ctk.CTkFrame(row_frame, fg_color=COLORS["success_dim"],
                                 corner_radius=6)
            chip.grid(row=i // cols, column=i % cols, padx=4, pady=3, sticky="ew")
            row_frame.columnconfigure(i % cols, weight=1)
            ctk.CTkLabel(chip, text=f"✔  {s}",
                         font=("Segoe UI", 10),
                         text_color=COLORS["success"],
                         anchor="w").pack(padx=8, pady=5, fill="x")

        # Failed (smaller)
        for s in result.settings_failed:
            chip = ctk.CTkFrame(self._applied_frame, fg_color=COLORS["surface2"],
                                 corner_radius=6)
            chip.pack(fill="x", pady=2)
            ctk.CTkLabel(chip, text=f"⊘  {s}  (run as Administrator for full access)",
                         font=("Segoe UI", 9),
                         text_color=COLORS["text_muted"],
                         anchor="w").pack(padx=8, pady=4, fill="x")

        # ── Recommendations ───────────────────────────────────────────────────
        for w in self._rec_frame.winfo_children():
            w.destroy()
        if result.recommendations:
            ctk.CTkLabel(self._rec_frame, text="💡 Recommendations",
                         font=("Segoe UI", 11, "bold"),
                         text_color=COLORS["text"]).pack(anchor="w", pady=(0, 4))
        for rec in result.recommendations:
            ctk.CTkLabel(self._rec_frame, text=rec,
                         font=("Segoe UI", 11),
                         text_color=COLORS["text"],
                         anchor="w", justify="left",
                         wraplength=650).pack(anchor="w", pady=1)

        # ── Time label ────────────────────────────────────────────────────────
        self._result_time_lbl.configure(
            text=f"Completed in {elapsed:.1f}s  ·  {time.strftime('%H:%M:%S')}")

        # Show result card
        self._result_card.pack(fill="x", padx=24, pady=(14, 0))

        # Hide steps card (replace with result)
        self._steps_card.pack_forget()

        # Notification
        try:
            notify_optimization_complete(count, result.estimated_improvement.split("—")[0].strip())
        except: pass

        # Update gauge
        if result.adapter_info:
            self._update_gauge(result.adapter_info)
            self._update_chips(result.adapter_info)

    # ── UI helpers ────────────────────────────────────────────────────────────
    def _update_gauge(self, adapter):
        if not adapter: return
        q = self._quality(adapter.signal_percent)
        self._gauge.set_signal(adapter.signal_percent, q)

    def _update_chips(self, adapter):
        if not adapter: return
        self._chips["ssid"].update(adapter.ssid or "—")
        self._chips["band"].update(adapter.radio_type or "—")
        self._chips["channel"].update(str(adapter.channel) if adapter.channel else "—")
        self._chips["rxrate"].update(f"{adapter.receive_rate_mbps:.0f} Mbps"
                                     if adapter.receive_rate_mbps else "—")
        self._chips["txrate"].update(f"{adapter.transmit_rate_mbps:.0f} Mbps"
                                     if adapter.transmit_rate_mbps else "—")
        self._status_dot.configure(
            text=f"● {adapter.ssid or 'Not connected'}  {adapter.signal_percent}%",
            text_color=COLORS["success"] if adapter.signal_percent >= 40 else COLORS["danger"])

    def _update_networks_page(self, scored):
        self._all_networks = scored
        self._apply_filter()

    def _apply_filter(self, value=None):
        band = self._band_filter.get() if hasattr(self, '_band_filter') else "All"
        for w in self._net_scroll.winfo_children():
            w.destroy()
        shown = 0
        for i, sc in enumerate(self._all_networks):
            if band != "All" and sc.network.band != band:
                continue
            NetworkRow(
                self._net_scroll,
                ssid=sc.network.ssid, band=sc.network.band,
                channel=sc.network.channel, signal_pct=sc.network.signal_percent,
                score=sc.total_score, auth=sc.network.authentication,
                is_best=(i == 0),
            ).pack(fill="x", pady=3)
            shown += 1
        self._net_count_lbl.configure(text=f"{shown} networks")

    @staticmethod
    def _quality(pct):
        if pct >= 80: return "Excellent"
        if pct >= 60: return "Good"
        if pct >= 40: return "Fair"
        if pct > 0:  return "Poor"
        return "No Signal"

    def _schedule_auto_refresh(self):
        self._do_scan()
        self.after(12_000, self._schedule_auto_refresh)

    # ── Monitor integration ───────────────────────────────────────────────────
    def attach_monitor(self, monitor):
        self._monitor = monitor
        monitor.register_callback(self._on_snapshot)

    def attach_tray(self, tray):
        self._tray = tray

    def _on_snapshot(self, snap):
        self.after(0, lambda: self._apply_snapshot(snap))

    def _apply_snapshot(self, snap):
        self._gauge.set_signal(snap.signal_percent, snap.quality)
        self._chips["down"].update(f"{snap.throughput_down_mbps:.2f} Mbps")
        self._status_dot.configure(
            text=f"● {snap.ssid or 'Not connected'}  {snap.signal_percent}%",
            text_color=COLORS["success"] if snap.signal_percent >= 40 else COLORS["danger"])
        if self._tray:
            self._tray.update(snap.signal_percent, snap.ssid, snap.quality)
        # Auto-optimize on weak signal
        if snap.signal_percent < 25 and not self._optimizing:
            if hasattr(self, '_auto_opt_var') and self._auto_opt_var.get():
                self.after(0, self._do_optimize)

    def _on_close(self):
        self.withdraw()

    def show(self):
        self.deiconify()
        self.lift()
        self.focus_force()
