"""
WiFi Optimizer - Main GUI Application
Redesigned: Optimize button is the hero element.
Shows live step progress and an honest before/after comparison.
"""

import customtkinter as ctk
import threading
import time
import os
import logging
from typing import Optional, List
from PIL import Image

from core.robustness import RobustnessController

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
                   improved: bool = True, neutral: bool = False):
        self._before_lbl.configure(text=before, text_color=COLORS["text_muted"])
        if neutral:
            # No real change — show it plainly instead of a misleading ▲/green.
            after_color = COLORS["text"]
            delta_color = COLORS["text_muted"]
            prefix = ""
        else:
            after_color = COLORS["success"] if improved else COLORS["warning"]
            delta_color = COLORS["success"] if improved else COLORS["warning"]
            prefix = "▲ " if improved else "▼ "
        self._after_lbl.configure(text=after, text_color=after_color)
        if delta:
            self._delta_lbl.configure(text=prefix + delta, text_color=delta_color)
        else:
            self._delta_lbl.configure(text="")


# ─── Score Badge ──────────────────────────────────────────────────────────────
class ScoreBadge(ctk.CTkCanvas):
    """Circular badge for the share of attempted writes that landed (0-100)."""

    def __init__(self, master, size=110, **kwargs):
        super().__init__(master, width=size, height=size,
                         bg=COLORS["surface2"], highlightthickness=0, **kwargs)
        self.size = size
        self._score = 0.0
        self._target = 0.0
        self._main_text: Optional[str] = None
        self._after_ids = []
        self.bind("<Destroy>", self._cleanup)
        self._draw(0)

    def _cleanup(self, _):
        for a in self._after_ids:
            try: self.after_cancel(a)
            except: pass

    def animate_to(self, score: float, main_text: Optional[str] = None):
        if main_text is not None:
            self._main_text = main_text
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
                         text=self._main_text if self._main_text is not None else f"{int(score)}%",
                         fill=self._score_color(score),
                         font=("Segoe UI", int(self.size * 0.20), "bold"))
        self.create_text(cx, cy + 14,
                         text="Tweaks",
                         fill=COLORS["accent"],
                         font=("Segoe UI", int(self.size * 0.085), "bold"))
        self.create_text(cx, cy + 30,
                         text="Applied",
                         fill=COLORS["text_muted"],
                         font=("Segoe UI", int(self.size * 0.08)))

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
        self._before_time = ""
        self._after_time = ""
        self._baseline_signal = None   # last pre-optimization signal %, shown live
        self._all_networks = []
        self._optimizing = False
        self._step_items: List[StepItem] = []
        # Background automation never widens a link on its own: it may move to
        # far_range when degraded, and back to balanced when the link recovers.
        self._robustness = RobustnessController(allow_speed=False, cooldown_s=900.0)

        self._setup_window()
        self._build_ui()
        self._schedule_auto_refresh()
        # Auto-detect the wireless interface name (may differ from "Wi-Fi" on
        # other laptops) so the netsh-based features target the right adapter.
        threading.Thread(target=self._detect_adapter_name, daemon=True).start()

    def _detect_adapter_name(self):
        """Detect and apply the real wireless interface name."""
        try:
            from core.adapter_manager import detect_wifi_interface_name
            name = detect_wifi_interface_name()
            if name and name != "Wi-Fi":
                self.after(0, lambda n=name: self._adapter_var.set(n))
                logger.info("Detected wireless interface name: %s", name)
        except Exception as e:
            logger.debug("Adapter name detection skipped: %s", e)

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

        # Fullscreen and Zoom control states
        self._help_zoom = 1.10
        self._is_fullscreen = False
        self.bind("<F11>", lambda e: self._toggle_fullscreen())
        self.bind("<Control-plus>", lambda e: self._zoom_help_in())
        self.bind("<Control-equal>", lambda e: self._zoom_help_in())
        self.bind("<Control-minus>", lambda e: self._zoom_help_out())
        self.bind("<Control-0>", lambda e: self._reset_help_zoom())

    def _toggle_fullscreen(self):
        self._is_fullscreen = not getattr(self, '_is_fullscreen', False)
        if self._is_fullscreen:
            try:
                self.state('zoomed')
            except Exception:
                self.attributes('-fullscreen', True)
            if hasattr(self, '_fs_btn'):
                self._fs_btn.configure(text="🗗 Windowed")
        else:
            try:
                self.state('normal')
                self.attributes('-fullscreen', False)
            except Exception:
                pass
            if hasattr(self, '_fs_btn'):
                self._fs_btn.configure(text="🗖 Fullscreen")

    def _zoom_help_in(self):
        self._help_zoom = min(2.5, round(self._help_zoom + 0.15, 2))
        self._refresh_help_zoom()

    def _zoom_help_out(self):
        self._help_zoom = max(0.75, round(self._help_zoom - 0.15, 2))
        self._refresh_help_zoom()

    def _reset_help_zoom(self):
        self._help_zoom = 1.10
        self._refresh_help_zoom()

    def _refresh_help_zoom(self):
        if hasattr(self, '_zoom_label'):
            self._zoom_label.configure(text=f"{int(self._help_zoom * 100)}%")
        if hasattr(self, '_help_scroll'):
            for w in self._help_scroll.winfo_children():
                w.destroy()
            self._render_help_content(self._help_scroll, self._help_zoom)

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
                           ("hardware",  "🔧  Hardware"),
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

        # ── Hero: Optimize section (own div, below About) ──────────────────
        hero = ctk.CTkFrame(self._sidebar, fg_color=COLORS["surface2"],
                             corner_radius=12, border_width=1,
                             border_color=COLORS["accent_glow"])
        hero.pack(fill="x", padx=8, pady=(8, 0))

        # Profile selector — vertical list so every option is visible
        ctk.CTkLabel(hero, text="Choose a profile:", font=("Segoe UI", 9),
                     text_color=COLORS["text_muted"]).pack(anchor="w", padx=10, pady=(8, 2))
        self._profile_var = ctk.StringVar(value="far_range")
        # Keep the controller in sync when the user picks a profile manually.
        # power_save is a deliberate battery choice and is left alone.
        self._profile_var.trace_add("write", lambda *_: self._sync_controller_mode())
        self._profile_options = [
            ("far_range",  "📡 Far / Robust (20 MHz)"),
            ("balanced",   "⚖️ Balanced"),
            ("max_speed",  "🚀 Max Speed"),
            ("power_save", "🔋 Power Saver"),
        ]
        self._profile_radios = {}
        for key, label in self._profile_options:
            rb = ctk.CTkRadioButton(
                hero, text=label, variable=self._profile_var, value=key,
                font=("Segoe UI", 11),
                radiobutton_width=16, radiobutton_height=16,
                border_width_checked=4,
                fg_color=COLORS["accent"], hover_color=COLORS["hero_hover"],
                text_color=COLORS["text"])
            rb.pack(anchor="w", padx=12, pady=1)
            self._profile_radios[key] = rb

        # Optimize button (directly under the profile list)
        self._optimize_btn = ctk.CTkButton(
            hero,
            text="⚡ OPTIMIZE NOW",
            font=("Segoe UI", 13, "bold"),
            fg_color=COLORS["hero_btn"],
            hover_color=COLORS["hero_hover"],
            text_color="#FFFFFF",
            corner_radius=10,
            height=40,
            command=self._do_optimize)
        self._optimize_btn.pack(fill="x", padx=8, pady=(8, 4))

        # Preview (dry-run) toggle — shows what WOULD change before applying
        self._preview_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            hero, text="Preview only (don't apply)",
            variable=self._preview_var,
            command=self._on_preview_toggle,
            font=("Segoe UI", 10), text_color=COLORS["text_muted"],
            progress_color=COLORS["accent"],
            switch_width=34, switch_height=18).pack(anchor="w", padx=10, pady=(0, 4))

        # Optional real measurement (download + RTT) before/after the run.
        self._measure_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            hero, text="Measure before/after (~40 s)",
            variable=self._measure_var,
            font=("Segoe UI", 10), text_color=COLORS["text_muted"],
            progress_color=COLORS["accent"],
            switch_width=34, switch_height=18).pack(anchor="w", padx=10, pady=(0, 4))

        # Progress bar + labels
        self._progress = ctk.CTkProgressBar(hero, mode="indeterminate",
                                             fg_color=COLORS["surface"],
                                             progress_color=COLORS["accent"],
                                             height=4, corner_radius=2)
        self._progress.pack(fill="x", padx=8, pady=(0, 2))
        self._progress_lbl = ctk.CTkLabel(hero, text="",
                                           font=("Segoe UI", 9, "italic"),
                                           text_color=COLORS["accent"])
        self._progress_lbl.pack(padx=8, anchor="w")

        # Hero sub-label (status text shown during optimization)
        self._hero_sub = ctk.CTkLabel(
            hero,
            text="",
            font=("Segoe UI", 9), text_color=COLORS["text_muted"])
        self._hero_sub.pack(padx=8, pady=(0, 4), anchor="w")

        # Bottom row: Scan + Help
        h_btns = ctk.CTkFrame(hero, fg_color="transparent")
        h_btns.pack(fill="x", padx=8, pady=(0, 8))
        self._scan_btn = ctk.CTkButton(
            h_btns, text="🔍 Scan", width=70, height=26,
            font=("Segoe UI", 10),
            fg_color=COLORS["surface"],
            hover_color=COLORS["border"],
            command=self._do_scan)
        self._scan_btn.pack(side="left", padx=(0, 4))
        self._help_btn = ctk.CTkButton(
            h_btns, text="❓ Help", width=70, height=26,
            font=("Segoe UI", 10),
            fg_color=COLORS["surface"],
            hover_color=COLORS["border"],
            command=lambda: self._nav("help"))
        self._help_btn.pack(side="left")

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
        self._build_hardware_page()
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
        ctk.CTkLabel(score_col, text="Tweaks Applied\n(not signal quality)",
                     font=("Segoe UI", 10, "bold"),
                     text_color=COLORS["accent"],
                     justify="center").pack(pady=(4, 0))

        # Before/After bars
        ba_grid = ctk.CTkFrame(rsummary, fg_color="transparent")
        ba_grid.pack(side="left", fill="both", expand=True)
        ba_grid.columnconfigure((0, 1), weight=1)

        self._bar_signal = ImprovementBar(ba_grid, "Signal (RF)", "📶")
        self._bar_signal.grid(row=0, column=0, padx=(0, 6), pady=4, sticky="ew")

        self._bar_channel = ImprovementBar(ba_grid, "Channel", "📻")
        self._bar_channel.grid(row=0, column=1, padx=(6, 0), pady=4, sticky="ew")

        self._bar_band = ImprovementBar(ba_grid, "Preferred Band", "📡")
        self._bar_band.grid(row=1, column=0, padx=(0, 6), pady=4, sticky="ew")

        self._bar_speed = ImprovementBar(ba_grid, "Link Speed", "⚡")
        self._bar_speed.grid(row=1, column=1, padx=(6, 0), pady=4, sticky="ew")

        # Plain-language verdict: did the numbers actually move?
        self._verdict_lbl = ctk.CTkLabel(
            self._result_card, text="",
            font=("Segoe UI", 10), text_color=COLORS["text_muted"],
            justify="left", anchor="w", wraplength=760)
        self._verdict_lbl.pack(fill="x", padx=20, pady=(6, 0))

        # Score distinction explainer banner
        guide_banner = ctk.CTkFrame(self._result_card, fg_color=COLORS["surface2"], corner_radius=8,
                                    border_width=1, border_color=COLORS["border"])
        guide_banner.pack(fill="x", padx=20, pady=(12, 0))
        ctk.CTkLabel(guide_banner,
                     text="💡 What the numbers mean: RSSI is raw antenna reception (dBm/%) — it is physical and does not rise because a setting changed. Link speed is the negotiated PHY rate, not measured Internet throughput. The 'changed' count means values that were read back at a new setting; 'already at target' values are listed separately and not counted as wins.",
                     font=("Segoe UI", 10),
                     text_color=COLORS["text"],
                     justify="left", anchor="w",
                     wraplength=760).pack(padx=12, pady=8, fill="x")

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
        status_card.pack(fill="x", padx=20, pady=(10, 0))

        ctk.CTkLabel(status_card, text="Live Status",
                     font=("Segoe UI", 12, "bold"),
                     text_color=COLORS["text"]).pack(anchor="w", padx=16, pady=(12, 0))

        status_inner = ctk.CTkFrame(status_card, fg_color="transparent")
        status_inner.pack(fill="x", padx=12, pady=(8, 12))

        # Signal gauge
        g_wrap = ctk.CTkFrame(status_inner, fg_color=COLORS["surface"],
                               corner_radius=12)
        g_wrap.pack(side="left", padx=(4, 12))
        self._gauge = SignalGauge(g_wrap, size=140, label="Signal (RF)")
        self._gauge.pack(padx=16, pady=(12, 4))
        ctk.CTkLabel(g_wrap, text="Antenna Reception", font=("Segoe UI", 9),
                     text_color=COLORS["text_muted"]).pack(pady=(0, 8))

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
            ("quality", "Link Quality", "🎯", COLORS["accent"]),
        ]
        for i, (key, lbl, icon, color) in enumerate(chip_defs):
            chip = MetricChip(chips_frame, label=lbl, icon=icon, color=color)
            chip.grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky="nsew")
            self._chips[key] = chip

        # Bottom spacer
        ctk.CTkFrame(scroll, fg_color="transparent", height=12).pack()

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
                          "Auto-detected on startup. Override only if needed "
                          "(must match the netsh interface name exactly).")
        self._adapter_var = ctk.StringVar(value="Wi-Fi")
        ctk.CTkEntry(ac, textvariable=self._adapter_var, width=200,
                     font=("Segoe UI", 12),
                     fg_color=COLORS["surface2"],
                     border_color=COLORS["border"]).pack(anchor="w", padx=8, pady=4)

        # Auto-optimize
        aoc = self._scard(scroll, "🔄 Auto-Optimize on Weak Signal",
                           "Opt-in. If the smoothed link stays at/below −78 dBm "
                           "(or measured efficiency collapses), apply the Far / Robust "
                           "profile once, with a 15-minute cooldown. Wi-Fi will "
                           "disconnect briefly while the adapter reloads.")
        self._auto_opt_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(aoc, text="Enable auto-optimization",
                      variable=self._auto_opt_var,
                      font=("Segoe UI", 12), text_color=COLORS["text"],
                      progress_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=4)

        # DNS
        dc = self._scard(scroll, "🌐 DNS Servers",
                          "Changes name resolution only. It does not raise RF signal "
                          "or throughput; keep your ISP/router DNS if unsure. The "
                          "previous values are captured for Restore Defaults.")
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
        self._apply_dns_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(dc, text="Apply these DNS servers when optimizing (opt-in)",
                      variable=self._apply_dns_var,
                      font=("Segoe UI", 11), text_color=COLORS["text"],
                      progress_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(6, 2))

        # ECN (opt-in, separate from DNS)
        tc = self._scard(scroll, "🌐 TCP / ECN (opt-in)",
                         "ECN can reduce loss-recovery latency only when the path "
                         "supports it; otherwise it is neutral. It does not raise "
                         "signal or peak throughput. The previous state is captured "
                         "for Restore Defaults.")
        self._apply_ecn_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(tc, text="Enable ECN when optimizing (opt-in)",
                      variable=self._apply_ecn_var,
                      font=("Segoe UI", 11), text_color=COLORS["text"],
                      progress_color=COLORS["accent"]).pack(anchor="w", padx=8, pady=(2, 8))

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
    # HARDWARE CAPABILITIES PAGE
    # ═══════════════════════════════════════════════════════════════════════════
    def _build_hardware_page(self):
        page = ctk.CTkFrame(self._content, fg_color=COLORS["bg"])
        self._pages["hardware"] = page

        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(20, 0))
        ctk.CTkLabel(hdr, text="🔧  Hardware Capabilities",
                     font=("Segoe UI", 20, "bold"),
                     text_color=COLORS["text"]).pack(side="left")
        ctk.CTkButton(hdr, text="↩ Restore Defaults", width=150, height=30,
                      font=("Segoe UI", 11),
                      fg_color=COLORS["surface2"],
                      hover_color=COLORS["danger"],
                      command=self._do_restore).pack(side="right", padx=(6, 0))
        ctk.CTkButton(hdr, text="🔄 Rescan", width=90, height=30,
                      font=("Segoe UI", 11),
                      fg_color=COLORS["surface2"],
                      hover_color=COLORS["border"],
                      command=self._refresh_hardware).pack(side="right")

        ctk.CTkLabel(
            page,
            text=("Which optimizer settings your WiFi driver actually exposes "
                  "(verified via the Windows NDIS Ndi\\params database). "
                  "Settings marked ✘ cannot be applied by this driver."),
            font=("Segoe UI", 11), text_color=COLORS["text_muted"],
            justify="left", wraplength=800).pack(anchor="w", padx=24, pady=(4, 8))

        self._hw_scroll = ctk.CTkScrollableFrame(page, fg_color=COLORS["bg"],
                                                  corner_radius=0)
        self._hw_scroll.pack(fill="both", expand=True, padx=24, pady=(0, 16))
        self._refresh_hardware()

    def _refresh_hardware(self):
        for w in self._hw_scroll.winfo_children():
            w.destroy()

        from core.adapter_manager import discover_optimizer_capabilities
        try:
            report = discover_optimizer_capabilities()
        except Exception as e:
            logger.error("Hardware discovery failed: %s", e)
            ctk.CTkLabel(self._hw_scroll, text=f"Discovery error: {e}",
                         font=("Segoe UI", 11),
                         text_color=COLORS["danger"]).pack(anchor="w")
            return

        if not report:
            ctk.CTkLabel(self._hw_scroll,
                         text="No wireless adapters detected (or running without access).",
                         font=("Segoe UI", 12),
                         text_color=COLORS["text_muted"]).pack(anchor="w")
            return

        for adapter in report:
            card = ctk.CTkFrame(self._hw_scroll, fg_color=COLORS["card"],
                                corner_radius=12, border_width=1,
                                border_color=COLORS["border"])
            card.pack(fill="x", pady=(0, 12))

            ctk.CTkLabel(card, text=adapter["desc"],
                         font=("Segoe UI", 13, "bold"),
                         text_color=COLORS["accent"]).pack(anchor="w", padx=14, pady=(10, 0))
            ctk.CTkLabel(
                card,
                text=f"Adapter key {adapter['subkey']}  ·  {adapter['param_count']} driver parameters exposed",
                font=("Segoe UI", 10),
                text_color=COLORS["text_muted"]).pack(anchor="w", padx=14, pady=(0, 6))

            grid = ctk.CTkFrame(card, fg_color="transparent")
            grid.pack(fill="x", padx=14, pady=(0, 12))
            grid.columnconfigure(0, weight=1)
            grid.columnconfigure(1, weight=1)

            for i, (label, info) in enumerate(adapter["capabilities"].items()):
                avail = info["available"]
                row = ctk.CTkFrame(
                    grid,
                    fg_color=COLORS["success_dim"] if avail else COLORS["surface2"],
                    corner_radius=6)
                row.grid(row=i // 2, column=i % 2, padx=4, pady=3, sticky="ew")
                icon = "✔" if avail else "✘"
                color = COLORS["success"] if avail else COLORS["text_muted"]
                ctk.CTkLabel(row, text=f"{icon}  {label}",
                             font=("Segoe UI", 11, "bold"),
                             text_color=color, anchor="w").pack(anchor="w", padx=8, pady=(4, 0))
                sub = info["keyword"] if avail else "not exposed by driver"
                ctk.CTkLabel(row, text=sub, font=("Segoe UI", 9),
                             text_color=COLORS["text_muted"],
                             anchor="w").pack(anchor="w", padx=8, pady=(0, 4))
                if avail and info.get("options"):
                    opts = " | ".join(info["options"][:6])
                    ctk.CTkLabel(row, text=opts, font=("Segoe UI", 8),
                                 text_color=COLORS["text_muted"], anchor="w",
                                 wraplength=340, justify="left").pack(anchor="w", padx=8, pady=(0, 4))

    def _do_restore(self):
        """Restore all changed WiFi adapter settings to their original values."""
        from tkinter import messagebox
        from core.adapter_manager import rollback_settings

        if not messagebox.askyesno(
                "Restore Defaults",
                "Restore all settings changed by this optimizer?\n\n"
                "This undoes adapter registry changes AND the DNS, wireless "
                "power-plan and TCP changes recorded in the system snapshot.\n\n"
                "Wi-Fi will disconnect for a few seconds while the adapter "
                "reloads."):
            return

        # Restore reloads the adapter, which can take ~20 s; keep the UI alive.
        threading.Thread(target=self._restore_worker, daemon=True).start()

    def _restore_worker(self):
        from core.adapter_manager import rollback_settings
        try:
            ok, log = rollback_settings()
        except Exception as e:
            logger.error("Rollback failed: %s", e)
            self.after(0, lambda: messagebox.showerror(
                "Restore Failed", f"Could not restore settings:\n{e}"))
            return
        self.after(0, lambda: self._restore_done(ok, log))

    def _restore_done(self, ok: bool, log):
        from tkinter import messagebox
        if not log:
            messagebox.showinfo("Restore Complete",
                                "Nothing to restore — no changes on record.")
        else:
            shown = "\n".join(log[:15])
            if len(log) > 15:
                shown += f"\n... (+{len(log) - 15} more)"
            messagebox.showinfo(
                "Restore Complete",
                f"{'Restore finished' if ok else 'Partially restored'}: "
                f"{len(log)} log entr{'y' if len(log) == 1 else 'ies'}.\n\n{shown}")
        self._refresh_hardware()

    # ═══════════════════════════════════════════════════════════════════════════
    # HOW IT WORKS / HELP PAGE
    # ═══════════════════════════════════════════════════════════════════════════
    def _build_help_page(self):
        page = ctk.CTkFrame(self._content, fg_color=COLORS["bg"])
        self._pages["help"] = page

        # Header with Title and Zoom / Fullscreen Toolbar
        hdr = ctk.CTkFrame(page, fg_color="transparent")
        hdr.pack(fill="x", padx=24, pady=(16, 8))

        # Title col on left
        t_col = ctk.CTkFrame(hdr, fg_color="transparent")
        t_col.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(t_col, text="❓  How It Works (Honest Version)",
                     font=("Segoe UI", 18, "bold"),
                     text_color=COLORS["text"]).pack(anchor="w")
        ctk.CTkLabel(t_col, text="What this tool can change, what it cannot, how the link is measured, and how to verify a result.",
                     font=("Segoe UI", 10),
                     text_color=COLORS["text_muted"]).pack(anchor="w", pady=(1, 0))

        # Toolbar on right: Zoom controls + Fullscreen
        tb = ctk.CTkFrame(hdr, fg_color=COLORS["surface"], corner_radius=10,
                          border_width=1, border_color=COLORS["border"])
        tb.pack(side="right", padx=(10, 0), pady=2)

        self._zoom_out_btn = ctk.CTkButton(
            tb, text="➖", width=34, height=30,
            font=("Segoe UI", 12, "bold"),
            fg_color="transparent", hover_color=COLORS["surface2"],
            text_color=COLORS["text"], command=self._zoom_help_out)
        self._zoom_out_btn.pack(side="left", padx=2, pady=2)

        self._zoom_label = ctk.CTkLabel(
            tb, text=f"{int(self._help_zoom * 100)}%",
            font=("Segoe UI", 11, "bold"),
            text_color=COLORS["accent"], width=52)
        self._zoom_label.pack(side="left", padx=2, pady=2)

        self._zoom_in_btn = ctk.CTkButton(
            tb, text="➕", width=34, height=30,
            font=("Segoe UI", 12, "bold"),
            fg_color="transparent", hover_color=COLORS["surface2"],
            text_color=COLORS["text"], command=self._zoom_help_in)
        self._zoom_in_btn.pack(side="left", padx=2, pady=2)

        self._zoom_reset_btn = ctk.CTkButton(
            tb, text="↺ 100%", width=58, height=28,
            font=("Segoe UI", 10),
            fg_color=COLORS["surface2"], hover_color=COLORS["border"],
            text_color=COLORS["text_muted"], command=self._reset_help_zoom)
        self._zoom_reset_btn.pack(side="left", padx=(2, 6), pady=2)

        self._fs_btn = ctk.CTkButton(
            tb, text="🗖 Fullscreen" if not self._is_fullscreen else "🗗 Windowed",
            width=105, height=28,
            font=("Segoe UI", 11, "bold"),
            fg_color=COLORS["accent_glow"], text_color=COLORS["accent"],
            hover_color=COLORS["border"], command=self._toggle_fullscreen)
        self._fs_btn.pack(side="left", padx=(0, 4), pady=2)

        self._help_scroll = ctk.CTkScrollableFrame(page, fg_color=COLORS["bg"], corner_radius=0)
        self._help_scroll.pack(fill="both", expand=True, padx=24, pady=(0, 16))

        self._render_help_content(self._help_scroll, self._help_zoom)

    def _render_help_content(self, scroll, zoom: float):
        """Concise, honest documentation.

        The previous page described invented "M-BFF" novelty and claimed
        percentage improvements the app never measured. This version states the
        actual scope: client-side settings plus real measurement.
        """
        title_font = ("Segoe UI", max(12, int(14 * zoom)), "bold")
        body_font = ("Segoe UI", max(10, int(11 * zoom)))
        small_font = ("Segoe UI", max(9, int(10 * zoom)))
        mono_font = ("Consolas", max(9, int(10 * zoom)))
        wrap = max(620, int(780 * zoom))

        def card(title: str, icon: str, subtitle: str):
            frame = ctk.CTkFrame(scroll, fg_color=COLORS["card"], corner_radius=12,
                                 border_width=1, border_color=COLORS["border"])
            frame.pack(fill="x", pady=(0, 12))
            head = ctk.CTkFrame(frame, fg_color="transparent")
            head.pack(fill="x", padx=14, pady=(10, 4))
            ctk.CTkLabel(head, text=f"{icon}  {title}", font=title_font,
                         text_color=COLORS["accent"]).pack(anchor="w")
            ctk.CTkLabel(head, text=subtitle, font=small_font,
                         text_color=COLORS["text_muted"],
                         wraplength=wrap, justify="left").pack(anchor="w")
            body = ctk.CTkFrame(frame, fg_color="transparent")
            body.pack(fill="x", padx=14, pady=(0, 12))
            return body

        def para(parent, text: str, muted: bool = False):
            ctk.CTkLabel(parent, text=text, font=body_font,
                         text_color=COLORS["text_muted"] if muted else COLORS["text"],
                         anchor="w", justify="left", wraplength=wrap).pack(
                             anchor="w", pady=2, fill="x")

        def bullet(parent, text: str):
            para(parent, "•  " + text)

        def mono(parent, text: str):
            box = ctk.CTkFrame(parent, fg_color=COLORS["surface"], corner_radius=8)
            box.pack(fill="x", pady=(6, 2))
            ctk.CTkLabel(box, text=text, font=mono_font,
                         text_color=COLORS["text"], anchor="w", justify="left",
                         wraplength=wrap).pack(anchor="w", padx=10, pady=8, fill="x")

        # 1 ─ Scope ─────────────────────────────────────────────────────────────
        b = card("What this tool can and cannot do", "🎯",
                 "No client-side setting can create radio signal.")
        para(b, "It CAN: choose the better visible BSS, force a narrower channel, "
                "prefer a band, change roaming/power-save behaviour, and reload the "
                "driver so those values actually apply.")
        para(b, "It CANNOT: raise the router's transmit power, move the router, "
                "reduce wall attenuation, or make a −85 dBm link fast. Those are "
                "physics and access-point-side decisions. Any claim of a fixed "
                "'15–40% improvement' would be fabricated.")
        para(b, "Throughput also depends on your ISP, the router's WAN link, and "
                "other clients. This app measures the Wi-Fi link, not the Internet "
                "plan.", muted=True)

        # 2 ─ Profiles ──────────────────────────────────────────────────────────
        b = card("Profiles", "🎛️", "Each profile is a small bundle of client settings.")
        bullet(b, "Far / Robust — 20 MHz width, 2.4 GHz preferred, roaming 1/5, "
                  "power save off. Best starting point when RSSI is below about "
                  "−72 dBm or retries are visible.")
        bullet(b, "Balanced — automatic width and band, roaming 2/5, power save off.")
        bullet(b, "Max Speed — 5/6 GHz preferred, automatic width, optional latency "
                  "tweaks (U-APSD off, packet coalescing off). Use only with a good "
                  "signal; wide channels lose SNR at range.")
        bullet(b, "Power Saver — keeps Windows power saving on. Higher latency, "
                  "lower throughput, longer battery life.")

        # 3 ─ Measurement ───────────────────────────────────────────────────────
        b = card("How the link is actually measured", "📏",
                 "Measurement replaces guesswork.")
        bullet(b, "Telemetry comes from the Windows Native Wi-Fi API "
                  "(wlanapi.dll), not from localized netsh text. RSSI is real dBm, "
                  "and the center frequency identifies 2.4/5/6 GHz correctly.")
        bullet(b, "The monitor samples per-adapter throughput (psutil) and computes "
                  "efficiency = sampled goodput ÷ min(rx, tx) PHY rate. A low ratio "
                  "with decent RSSI usually means a wide/congested channel; a low "
                  "ratio with low RSSI means you are at the sensitivity edge.")
        bullet(b, "The benchmark performs a single-stream HTTPS download, TCP-connect "
                  "RTT (median, p95), jitter, loss, and DNS time. Run it at least "
                  "three times and compare medians; one sample proves nothing.")
        mono(b, "python scripts/benchmark.py --reps 3 --label before\n"
                "python scripts/benchmark.py --reps 3 --label after")
        para(b, "Results are appended to benchmarks.jsonl in the project folder.",
             muted=True)

        # 4 ─ Scoring ───────────────────────────────────────────────────────────
        b = card("Network scoring is a weighted sum", "🧮",
                 "Simple, reproducible, and not claimed to be novel.")
        mono(b, "score = signal(30) + channel(20) + band(20) + PHY(20) + security(10)\n"
                "signal  = 30 / (1 + exp(-0.35 * (dBm + 72)))\n"
                "channel = 20 - 5 * Σ(overlap * neighbour_weight)\n"
                "band    = 20 * band_preference * link_margin_taper")
        para(b, "The signal term is a logistic curve with its midpoint at −72 dBm. "
                "The channel term counts overlapping BSSes weighted by their own "
                "signal; 2.4 GHz overlap uses a triangular 22 MHz mask, while 5/6 GHz "
                "channels are treated as orthogonal. The security term gates open "
                "and WEP networks out of recommendations by default.")
        para(b, "These weights are a UX heuristic. They are not a calibrated "
                "throughput predictor, and the app does not pretend otherwise.",
             muted=True)

        # 5 ─ Levers ────────────────────────────────────────────────────────────
        b = card("What actually changes on the PC", "⚙️",
                 "Every write is read back and reported as changed / unchanged / failed.")
        bullet(b, "Channel width (2.4 and 5 GHz) when the driver exposes "
                  "ChannelWidth24/52 or an equivalent NDIS keyword.")
        bullet(b, "Preferred band, roaming aggressiveness, transmit power, "
                  "sleep-on-disconnect, U-APSD, packet coalescing, throughput booster.")
        bullet(b, "Windows wireless power-plan index for both AC and DC (the old code "
                  "only changed AC and hardcoded the interface name 'Wi-Fi').")
        bullet(b, "Optional DNS resolvers and TCP autotuning/ECN. Autotuning is "
                  "normally already 'normal', so it is reported as unchanged rather "
                  "than counted as a win.")
        bullet(b, "The adapter is disabled/enabled at the end when at least one NDIS "
                  "value changed, because drivers read those values on miniport "
                  "initialization.")

        # 6 ─ Safety ────────────────────────────────────────────────────────────
        b = card("Safety and reversibility", "🛡️",
                 "Preview first; restore is real, not cosmetic.")
        bullet(b, "Preview mode records the exact current → proposed values and "
                  "writes nothing.")
        bullet(b, "A timestamped adapter_settings_backup_*.json is written before "
                  "changes (the five newest are kept). The old code overwrote one "
                  "file with already-optimized values.")
        bullet(b, "A system_rollback_snapshot.json records DNS, the wireless power "
                  "plan index, and TCP autotuning/ECN/timestamps. Restore Defaults "
                  "replays the registry snapshot AND these system values, then "
                  "reloads the adapter.")
        bullet(b, "Properties absent from the driver's Ndi\\params are skipped, "
                  "never guessed.")

        # 7 ─ Limits ────────────────────────────────────────────────────────────
        b = card("Known limitations", "⚠️",
                 "Read this before expecting miracles.")
        bullet(b, "Registry/NDIS changes require Administrator. Without elevation "
                  "the app reports them as failed instead of silently claiming success.")
        bullet(b, "Adapters that do not expose a width/band keyword cannot be "
                  "narrowed by software. The app reports them as unsupported.")
        bullet(b, "Throughput samples are noisy. Background downloads, other clients, "
                  "and the ISP affect them. The benchmark is a single stream on purpose.")
        bullet(b, "The controller's thresholds (−72/−64 dBm, 10%/22% efficiency) are "
                  "starting points, not universal constants. They are tunable in "
                  "core/robustness.py.")

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
        for f in ["🔍  Scans every visible BSSID (native Wi-Fi API, locale-independent)",
                  "📊  Scores signal · channel · band · PHY · security (explicit weights)",
                  "⚙️   Applies driver settings with read-back verification",
                  "📈  Shows before → after values; real throughput only when measured",
                  "🔄  Optional controller with hysteresis + cooldown (far/balanced/speed)",
                  "🔔  Windows 10/11 toast notifications",
                  "🖥️   System tray icon with live signal level indicator",
                  "⚡  TCP autotuning/ECN only, reported as unchanged when already set"]:
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
            scored = sorted(
                [score_network(n, cong, profile, all_networks=nets,
                               adapter_info=adapter) for n in nets],
                key=lambda x: (x.is_eligible, x.total_score), reverse=True)
            connected_score = None
            if adapter:
                for sc in scored:
                    same_bssid = (sc.network.bssid and adapter.bssid
                                  and sc.network.bssid.upper() == adapter.bssid.upper())
                    # Fall back to SSID only when the BSSID is unavailable.
                    if same_bssid or (not adapter.bssid and sc.network.ssid == adapter.ssid):
                        sc.network.connected = True
                        connected_score = sc.total_score
            self._all_networks = scored
            self.after(0, lambda: (
                self._update_chips(adapter, connected_score),
                self._update_gauge(adapter),
                self._update_networks_page(scored),
            ))
        except Exception as e:
            logger.error("Scan error: %s", e)
        finally:
            self.after(0, lambda: self._scan_btn.configure(
                state="normal", text="🔍 Scan"))

    def _on_preview_toggle(self):
        """Update the optimize button label to reflect preview (dry-run) mode."""
        if self._preview_var.get():
            self._optimize_btn.configure(text="👀 PREVIEW CHANGES")
        else:
            self._optimize_btn.configure(text="⚡ OPTIMIZE NOW")

    def _do_optimize(self):
        if self._optimizing:
            return
        self._optimizing = True
        self._opt_start_time = time.time()
        preview = bool(self._preview_var.get())

        # Capture BEFORE state (timestamped so the user can compare later)
        from core.wifi_scanner import get_connected_network
        self._before_snapshot = get_connected_network()
        self._before_time = time.strftime("%H:%M:%S")
        if self._before_snapshot:
            self._baseline_signal = self._before_snapshot.signal_percent

        # Change to dashboard
        self._nav("dashboard")

        # Lock button, show progress
        self._optimize_btn.configure(
            state="disabled",
            text="⏳  Analyzing…" if preview else "⏳  Optimizing…",
            fg_color=COLORS["accent_glow"])
        self._progress.pack(fill="x", padx=24, pady=(4, 0))
        self._progress.start()
        self._hero_sub.configure(
            text=("Scanning and building a read-only preview — nothing will be written…"
                  if preview else
                  "Analyzing your connection and applying the best settings…"),
            text_color=COLORS["accent"])

        # Show steps card and build step rows
        self._steps_card.pack(fill="x", padx=20, pady=(10, 0))
        for w in self._steps_inner.winfo_children():
            w.destroy()
        self._step_items.clear()

        step_defs = [
            "Scanning visible networks (all BSSIDs)",
            "Scoring signal, channel and security",
            "Applying band / roaming / transmit power",
            "Setting channel width (20 MHz in far mode)",
            "Configuring power mode",
            "Setting DNS / TCP (only if selected)",
            "Reloading the adapter so values take effect",
            "Verifying the link",
            "Measuring before/after (optional)",
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
            elif "power" in lower or "sleep" in lower:
                _advance(msg)
            elif "width" in lower:
                _advance(msg)
            elif "background" in lower:
                _advance(msg)
            elif "tcp" in lower:
                _advance(msg)
            elif "dns" in lower:
                _advance(msg)
            elif "reload" in lower or "restart" in lower:
                _advance(msg)
            elif "measur" in lower or "verif" in lower or "done" in lower or "complete" in lower:
                _advance(msg)

        # Mark step 0 running
        self.after(0, lambda: self._update_step(0, "running"))

        try:
            preview = bool(self._preview_var.get())
            result = run_optimization(
                profile_key=self._profile_var.get(),
                adapter_name=getattr(self, '_adapter_var',
                                     type('_', (), {'get': lambda s: 'Wi-Fi'})()).get(),
                progress_callback=progress,
                preview=preview,
                measure=bool(self._measure_var.get()) and not preview,
                dns_primary=(self._dns1_var.get().strip()
                             if hasattr(self, '_dns1_var') else None),
                dns_secondary=(self._dns2_var.get().strip()
                               if hasattr(self, '_dns2_var') else None),
                apply_dns=(bool(self._apply_dns_var.get())
                           if hasattr(self, '_apply_dns_var') else None),
                apply_ecn=(bool(self._apply_ecn_var.get())
                           if hasattr(self, '_apply_ecn_var') else None),
            )
            # Mark remaining steps done
            accounted = len(result.settings_applied) + len(result.settings_unchanged)
            for i in range(len(self._step_items)):
                self.after(0, lambda idx=i, r=result: self._update_step(
                    idx,
                    "done" if idx < accounted + 4 else "skip"))

            # Capture AFTER (timestamped)
            from core.wifi_scanner import get_connected_network
            self._after_snapshot = get_connected_network()
            self._after_time = time.strftime("%H:%M:%S")
            elapsed = time.time() - self._opt_start_time

            if preview:
                self.after(300, lambda: self._show_preview(result))
            else:
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
        # Safety: never leave preview mode enabled after a run, even on error
        try:
            from core.adapter_manager import set_preview_mode
            set_preview_mode(False)
        except Exception:
            pass
        self._optimize_btn.configure(
            state="normal",
            text="👀 PREVIEW CHANGES" if self._preview_var.get() else "⚡  OPTIMIZE NOW",
            fg_color=COLORS["hero_btn"])
        self._progress.stop()
        self._progress.pack_forget()
        if self._preview_var.get():
            self._hero_sub.configure(
                text="Preview ready — nothing was changed.",
                text_color=COLORS["accent"])
        else:
            self._hero_sub.configure(
                text="Optimization complete! See your results below.",
                text_color=COLORS["success"])

    def _show_preview(self, result):
        """Show a read-only list of every change the run WOULD make."""
        changes = result.preview_changes or []
        will_change = [c for c in changes if c.get("changed")]
        already = [c for c in changes if not c.get("changed")]

        win = ctk.CTkToplevel(self)
        win.title(f"Preview — {result.profile_name}")
        win.geometry("780x600")
        win.configure(fg_color=COLORS["bg"])
        win.transient(self)
        win.after(80, win.lift)

        hdr = ctk.CTkFrame(win, fg_color="transparent")
        hdr.pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(hdr, text="👀  Preview — nothing has been changed",
                     font=("Segoe UI", 17, "bold"),
                     text_color=COLORS["accent"]).pack(anchor="w")
        ctk.CTkLabel(
            win,
            text=(f"Profile: {result.profile_name}\n"
                  f"Registry/config values that WOULD change: {len(will_change)}   ·   "
                  f"already at target (no-op): {len(already)}   ·   "
                  f"total evaluated: {len(changes)}\n"
                  "Nothing is written until you switch 'Preview only' off and press OPTIMIZE NOW."),
            font=("Segoe UI", 11), text_color=COLORS["text_muted"],
            justify="left", wraplength=730).pack(anchor="w", padx=18, pady=(0, 8))

        scroll = ctk.CTkScrollableFrame(win, fg_color=COLORS["bg"])
        scroll.pack(fill="both", expand=True, padx=18, pady=(0, 8))

        if not changes:
            ctk.CTkLabel(scroll, text="No changes would be made on this hardware.",
                         font=("Segoe UI", 12),
                         text_color=COLORS["text_muted"]).pack(anchor="w", pady=10)
        else:
            if will_change:
                ctk.CTkLabel(scroll, text=f"⚡ Will change ({len(will_change)})",
                             font=("Segoe UI", 13, "bold"),
                             text_color=COLORS["success"]).pack(anchor="w", pady=(4, 4))
                self._render_preview_rows(scroll, will_change, highlight=True)

            if already:
                ctk.CTkLabel(scroll, text=f"✓ Already at target — no change ({len(already)})",
                             font=("Segoe UI", 13, "bold"),
                             text_color=COLORS["text_muted"]).pack(anchor="w", pady=(14, 4))
                self._render_preview_rows(scroll, already, highlight=False)

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=18, pady=(0, 16))
        ctk.CTkButton(btns, text="Close", width=110, height=34,
                      font=("Segoe UI", 12),
                      fg_color=COLORS["surface2"], hover_color=COLORS["border"],
                      command=win.destroy).pack(side="right")

    def _render_preview_rows(self, parent, items, highlight: bool):
        """Render preview change rows grouped by adapter."""
        grouped = {}
        for c in items:
            grouped.setdefault(c.get("adapter", "?"), []).append(c)

        for adapter, rows in grouped.items():
            ctk.CTkLabel(parent, text=adapter,
                         font=("Segoe UI", 11, "bold"),
                         text_color=COLORS["accent"] if highlight else COLORS["text"],
                         anchor="w").pack(anchor="w", pady=(6, 2))
            for c in rows:
                row = ctk.CTkFrame(
                    parent,
                    fg_color=COLORS["card"] if highlight else COLORS["surface2"],
                    corner_radius=8, border_width=1,
                    border_color=COLORS["border"])
                row.pack(fill="x", pady=2)
                top = ctk.CTkFrame(row, fg_color="transparent")
                top.pack(fill="x", padx=10, pady=(6, 0))
                name = c.get("intent") or c.get("property", "?")
                ctk.CTkLabel(top, text=name, font=("Segoe UI", 11, "bold"),
                             text_color=COLORS["text"]).pack(side="left")
                ctk.CTkLabel(top, text=c.get("property", ""),
                             font=("Segoe UI", 9),
                             text_color=COLORS["text_muted"]).pack(side="left", padx=8)
                cur = c.get("current")
                cur_txt = cur if cur is not None else "(not set)"
                val = ctk.CTkFrame(row, fg_color="transparent")
                val.pack(fill="x", padx=10, pady=(2, 6))
                ctk.CTkLabel(val, text=f"{cur_txt}", font=("Segoe UI", 10),
                             text_color=COLORS["text_muted"]).pack(side="left")
                arrow = "  →  " if highlight else "  =  "
                ctk.CTkLabel(val, text=arrow, font=("Segoe UI", 10),
                             text_color=COLORS["accent"] if highlight else COLORS["text_muted"]
                             ).pack(side="left")
                ctk.CTkLabel(val, text=f"{c.get('proposed', '')}",
                             font=("Segoe UI", 10, "bold"),
                             text_color=COLORS["success"] if highlight else COLORS["text_muted"]
                             ).pack(side="left")

    def _show_result(self, result, elapsed: float):
        from ui.notifications import notify_optimization_complete

        applied_count = len(result.settings_applied)
        unchanged_count = len(result.settings_unchanged)
        failed_count = len(result.settings_failed)
        skipped_count = len(result.settings_skipped)
        attempted = applied_count + failed_count
        success_pct = int((applied_count / attempted) * 100) if attempted else 100

        # The badge is "how many of the attempted writes actually landed", NOT
        # signal quality or a made-up speed percentage.
        self._score_badge.animate_to(success_pct, main_text=f"{applied_count} changed")

        before = self._before_snapshot
        after = self._after_snapshot or before

        if before:
            b_sig, a_sig = before.signal_percent, (after.signal_percent if after else before.signal_percent)
            sig_delta = a_sig - b_sig
            self._bar_signal.set_values(
                before=f"{b_sig}%",
                after=f"{a_sig}%",
                delta=f"{abs(sig_delta)}%" if sig_delta else "unchanged",
                improved=sig_delta > 0,
                neutral=(sig_delta == 0))

            b_ch = f"Ch {before.channel}" if before.channel else "?"
            a_ch = f"Ch {after.channel}" if (after and after.channel) else b_ch
            # A channel/band change is not automatically an improvement; leave
            # it neutral and let the measured throughput/RTT decide.
            self._bar_channel.set_values(before=b_ch, after=a_ch,
                                          delta="changed" if b_ch != a_ch else "same",
                                          improved=False, neutral=True)

            b_band = before.band or "?"
            a_band = (after.band if after else b_band) or "?"
            self._bar_band.set_values(before=b_band, after=a_band,
                                       delta="switched" if b_band != a_band else "same",
                                       improved=False, neutral=True)

            b_spd = f"{before.receive_rate_mbps:.0f} Mbps" if before.receive_rate_mbps else "?"
            a_spd = f"{after.receive_rate_mbps:.0f} Mbps" if (after and after.receive_rate_mbps) else b_spd
            spd_delta = (after.receive_rate_mbps - before.receive_rate_mbps) if after else 0
            self._bar_speed.set_values(before=b_spd, after=a_spd,
                                        delta=f"{abs(spd_delta):.0f} Mbps" if spd_delta else "unchanged",
                                        improved=spd_delta > 0,
                                        neutral=(spd_delta == 0))
        else:
            a = result.adapter_info
            self._bar_signal.set_values("—", f"{a.signal_percent}%" if a else "—", improved=False, neutral=True)
            self._bar_channel.set_values("—", f"Ch {a.channel}" if a else "—", improved=False, neutral=True)
            self._bar_band.set_values("—", (a.band if a else "—") or "—", improved=False, neutral=True)
            self._bar_speed.set_values("—", f"{a.receive_rate_mbps:.0f} Mbps" if a else "—",
                                        improved=False, neutral=True)

        # Verdict: separate "changed" from "already at target", include the
        # measured result when available, and never claim RF improvement.
        parts = []
        if before:
            sig_delta = (after.signal_percent - before.signal_percent) if after else 0
            if sig_delta == 0:
                parts.append(f"RSSI unchanged ({before.signal_dbm:.0f} dBm); "
                             f"client settings cannot raise RF power.")
            else:
                parts.append(f"RSSI {before.signal_dbm:.0f} → {after.signal_dbm:.0f} dBm "
                             f"(environmental / roam).")
        parts.append(f"{applied_count} changed, {unchanged_count} already at target, "
                     f"{failed_count} failed, {skipped_count} unsupported.")
        if result.restart_message:
            parts.append(result.restart_message)

        measured = result.measured or {}
        bm, am = measured.get("before"), measured.get("after")
        if bm and am:
            parts.append(f"Measured: {bm.get('throughput_mbps', 0):.1f} → "
                         f"{am.get('throughput_mbps', 0):.1f} Mbps; "
                         f"p95 RTT {bm.get('latency_p95_ms', 0):.0f} → "
                         f"{am.get('latency_p95_ms', 0):.0f} ms; "
                         f"loss {bm.get('loss_pct', 0):.1f}% → {am.get('loss_pct', 0):.1f}%.")
        else:
            parts.append("Throughput/latency not measured this run.")
        self._verdict_lbl.configure(text=" ".join(parts))

        # ── Setting chips ─────────────────────────────────────────────────────
        for w in self._applied_frame.winfo_children():
            w.destroy()
        self._applied_count_lbl.configure(
            text=(f"{applied_count} changed · {unchanged_count} already at target · "
                  f"{failed_count} failed · {skipped_count} unsupported"),
            text_color=COLORS["success"] if failed_count == 0 else COLORS["warning"])

        cols = 2
        row = 0

        def _chip(text, fg, color, prefix):
            nonlocal row
            chip = ctk.CTkFrame(self._applied_frame, fg_color=fg, corner_radius=6)
            chip.grid(row=row // cols, column=row % cols, padx=4, pady=3, sticky="ew")
            self._applied_frame.columnconfigure(row % cols, weight=1)
            ctk.CTkLabel(chip, text=f"{prefix}  {text}", font=("Segoe UI", 10),
                         text_color=color, anchor="w").pack(padx=8, pady=5, fill="x")
            row += 1

        for s in result.settings_applied:
            _chip(s, COLORS["success_dim"], COLORS["success"], "✔")
        for s in result.settings_unchanged:
            _chip(s, COLORS["surface2"], COLORS["text_muted"], "=")
        for s in result.settings_failed:
            _chip(s, COLORS["surface2"], COLORS["warning"], "⊘")
        for s in result.settings_skipped:
            _chip(s, COLORS["surface2"], COLORS["text_muted"], "–")

        # ── Recommendations ───────────────────────────────────────────────────
        for w in self._rec_frame.winfo_children():
            w.destroy()
        if result.recommendations:
            ctk.CTkLabel(self._rec_frame, text="💡 What would actually help",
                         font=("Segoe UI", 11, "bold"),
                         text_color=COLORS["text"]).pack(anchor="w", pady=(0, 4))
        for rec in result.recommendations:
            ctk.CTkLabel(self._rec_frame, text=rec,
                         font=("Segoe UI", 11),
                         text_color=COLORS["text"],
                         anchor="w", justify="left",
                         wraplength=650).pack(anchor="w", pady=1)

        if self._before_time and self._after_time:
            self._result_time_lbl.configure(
                text=f"Before {self._before_time} → After {self._after_time}  ·  {elapsed:.1f}s")
        else:
            self._result_time_lbl.configure(
                text=f"Completed in {elapsed:.1f}s  ·  {time.strftime('%H:%M:%S')}")

        self._result_card.pack(fill="x", padx=24, pady=(14, 0))
        self._steps_card.pack_forget()

        try:
            detail = ("Measured before/after — see the verdict line."
                      if result.measured and result.measured.get("after")
                      else "Not measured. Enable 'Measure before/after' for real numbers.")
            notify_optimization_complete(applied_count, detail)
        except Exception:
            pass

        if result.adapter_info:
            self._update_gauge(result.adapter_info)
            self._update_chips(result.adapter_info)

    # ── UI helpers ────────────────────────────────────────────────────────────
    def _update_gauge(self, adapter):
        if not adapter: return
        q = self._quality(adapter.signal_percent)
        self._gauge.set_signal(adapter.signal_percent, q)

    def _status_text(self, ssid, pct):
        """Sidebar status line, including the pre-optimization signal."""
        text = f"● {ssid or 'Not connected'}  {pct}%"
        if self._baseline_signal is not None:
            text += f"  ·  was {self._baseline_signal}%"
        return text

    def _update_chips(self, adapter, net_score=None):
        if not adapter: return
        self._chips["ssid"].update(adapter.ssid or "—")
        self._chips["band"].update(adapter.radio_type or "—")
        self._chips["channel"].update(str(adapter.channel) if adapter.channel else "—")
        self._chips["rxrate"].update(f"{adapter.receive_rate_mbps:.0f} Mbps"
                                     if adapter.receive_rate_mbps else "—")
        self._chips["txrate"].update(f"{adapter.transmit_rate_mbps:.0f} Mbps"
                                     if adapter.transmit_rate_mbps else "—")
        if "quality" in self._chips:
            if net_score is not None:
                self._chips["quality"].update(f"{net_score:.0f} / 100")
            elif hasattr(adapter, 'quality') and adapter.quality:
                self._chips["quality"].update(adapter.quality)
            else:
                self._chips["quality"].update(self._quality(adapter.signal_percent))
        self._status_dot.configure(
            text=self._status_text(adapter.ssid, adapter.signal_percent),
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
        if "quality" in self._chips:
            self._chips["quality"].update(snap.quality)
        if "down" in self._chips:
            self._chips["down"].update(f"{snap.throughput_down_mbps:.2f} Mbps")
        self._status_dot.configure(
            text=self._status_text(snap.ssid, snap.signal_percent),
            text_color=COLORS["success"] if snap.signal_percent >= 40 else COLORS["danger"])
        if self._tray:
            self._tray.update(snap.signal_percent, snap.ssid, snap.quality)
        # Auto robustness (opt-in). The controller requires a sustained
        # condition (2 samples), smooths RSSI/efficiency, and has a 15-minute
        # cooldown, so a weak link cannot trigger a restart loop. A missing
        # sample is treated as unknown, not as zero.
        if not snap.ssid or snap.signal_dbm <= -100:
            return
        if not (hasattr(self, '_auto_opt_var') and self._auto_opt_var.get()):
            return
        if self._optimizing:
            return
        try:
            current = self._profile_var.get()
        except Exception:
            current = ""
        if current == "power_save":
            return
        try:
            active_sample = snap.throughput_down_mbps >= 0.5
            decision = self._robustness.observe(
                snap.signal_dbm,
                efficiency=snap.efficiency if active_sample else None,
            )
            if decision.changed:
                try:
                    current = self._profile_var.get()
                except Exception:
                    current = ""
                if decision.mode != current:
                    self.after(0, lambda m=decision.mode, r=decision.reason:
                               self._auto_apply_mode(m, r))
        except Exception as e:
            logger.debug("Robustness controller error: %s", e)

    def _sync_controller_mode(self):
        try:
            self._robustness.set_mode(self._profile_var.get())
        except Exception:
            pass

    def _auto_apply_mode(self, mode: str, reason: str = ""):
        """Apply a controller decision on the UI thread."""
        if self._optimizing:
            return
        try:
            self._profile_var.set(mode)
        except Exception:
            pass
        try:
            from ui.notifications import notify
            from core.robustness import MODE_LABELS
            notify("🔄 Auto mode change",
                   f"{MODE_LABELS.get(mode, mode)} — {reason}",
                   duration="short")
        except Exception:
            pass
        self._do_optimize()

    def _on_close(self):
        self.withdraw()

    def show(self):
        self.deiconify()
        self.lift()
        self.focus_force()
