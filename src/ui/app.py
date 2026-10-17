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
                         text=f"{int(score)}%",
                         fill=self._score_color(score),
                         font=("Segoe UI", int(self.size * 0.20), "bold"))
        self.create_text(cx, cy + 14,
                         text="Optimized",
                         fill=COLORS["accent"],
                         font=("Segoe UI", int(self.size * 0.085), "bold"))
        self.create_text(cx, cy + 30,
                         text="System Tuned",
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
        self._all_networks = []
        self._optimizing = False
        self._step_items: List[StepItem] = []

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
        self._profile_var = ctk.StringVar(value="selfish")
        self._profile_options = [
            ("selfish",    "⚡ Selfish Turbo"),
            ("max_speed",  "🚀 Max Speed"),
            ("max_range",  "📶 Max Range"),
            ("balanced",   "⚖️ Balanced"),
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
        ctk.CTkLabel(score_col, text="Optimization\nLevel",
                     font=("Segoe UI", 10, "bold"),
                     text_color=COLORS["accent"],
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

        # Score distinction explainer banner
        guide_banner = ctk.CTkFrame(self._result_card, fg_color=COLORS["surface2"], corner_radius=8,
                                    border_width=1, border_color=COLORS["border"])
        guide_banner.pack(fill="x", padx=20, pady=(12, 0))
        ctk.CTkLabel(guide_banner,
                     text="💡 Score Guide: Signal Strength is raw RF antenna reception (dBm). Link Quality (M-BFF) rates channel cleanness & bandwidth (0-100). Optimization Level shows what % of Windows adapter & kernel tweaks are active.",
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
                "Restore all WiFi adapter settings changed by this optimizer "
                "back to their original values?\n\n"
                "This undoes the registry changes made during optimization. "
                "WiFi may briefly disconnect while the driver reinitializes."):
            return

        try:
            ok, log = rollback_settings()
        except Exception as e:
            logger.error("Rollback failed: %s", e)
            messagebox.showerror("Restore Failed", f"Could not restore settings:\n{e}")
            return

        if not log:
            messagebox.showinfo("Restore Complete", "Nothing to restore — no changes on record.")
        else:
            shown = "\n".join(log[:15])
            if len(log) > 15:
                shown += f"\n... (+{len(log) - 15} more)"
            messagebox.showinfo("Restore Complete",
                                f"{'Restored' if ok else 'Partially restored'} "
                                f"{len(log)} setting(s):\n\n{shown}")
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

        ctk.CTkLabel(t_col, text="❓  How It Works & Mathematical Architecture",
                     font=("Segoe UI", 18, "bold"),
                     text_color=COLORS["text"]).pack(anchor="w")
        ctk.CTkLabel(t_col, text="Complete physical formulas, RF radio models, M-BFF scoring equations, NDIS parameters, and kernel optimizations.",
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
        # Scaled font definitions
        title_font = ("Segoe UI", max(11, int(13 * zoom)), "bold")
        sub_font = ("Segoe UI", max(9, int(10 * zoom)))
        form_title_font = ("Segoe UI", max(9, int(10.5 * zoom)), "bold")
        form_code_font = ("Consolas", max(10, int(11.5 * zoom)), "bold")
        form_desc_font = ("Segoe UI", max(9, int(9.5 * zoom)))
        code_font = ("Consolas", max(9, int(9.5 * zoom)))
        desc_wrap = max(700, int(760 * zoom))

        def _help_card(title: str, icon: str, subtitle: str, color=COLORS["accent"]) -> ctk.CTkFrame:
            card = ctk.CTkFrame(scroll, fg_color=COLORS["card"], corner_radius=12,
                                border_width=1, border_color=COLORS["border"])
            card.pack(fill="x", pady=(0, 14))

            chdr = ctk.CTkFrame(card, fg_color="transparent")
            chdr.pack(fill="x", padx=16, pady=(12, 4))
            ctk.CTkLabel(chdr, text=f"{icon}  {title}", font=title_font,
                         text_color=color).pack(side="left")

            ctk.CTkLabel(card, text=subtitle, font=sub_font,
                         text_color=COLORS["text_muted"]).pack(anchor="w", padx=16, pady=(0, 8))

            content_box = ctk.CTkFrame(card, fg_color="transparent")
            content_box.pack(fill="x", padx=16, pady=(0, 12))
            return content_box

        def _code_box(parent, text: str):
            c_frame = ctk.CTkFrame(parent, fg_color=COLORS["surface2"], corner_radius=8,
                                   border_width=1, border_color=COLORS["border"])
            c_frame.pack(fill="x", pady=4, padx=4)
            ctk.CTkLabel(c_frame, text=text, font=code_font,
                         text_color=COLORS["text"], justify="left", anchor="w").pack(padx=12, pady=8, fill="x")

        def _formula_box(parent, title: str, formula: str, desc: str = ""):
            f_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg"], corner_radius=8,
                                   border_width=1, border_color=COLORS["border"])
            f_frame.pack(fill="x", pady=4, padx=4)
            top = ctk.CTkFrame(f_frame, fg_color="transparent")
            top.pack(fill="x", padx=10, pady=(6, 2))
            ctk.CTkLabel(top, text=title, font=form_title_font,
                         text_color=COLORS["accent"]).pack(side="left")
            ctk.CTkLabel(f_frame, text=formula, font=form_code_font,
                         text_color="#F8FAFC", justify="left", anchor="w").pack(padx=12, pady=(0, 4), fill="x")
            if desc:
                ctk.CTkLabel(f_frame, text=desc, font=form_desc_font,
                             text_color=COLORS["text_muted"], justify="left", anchor="w",
                             wraplength=desc_wrap).pack(padx=12, pady=(0, 6), fill="x")

        # ── 0. Demystifying Scores: Signal vs. Quality vs. Optimization ───────
        c0 = _help_card(
            "Understanding Your Scores: Signal vs. Connection Quality vs. Optimization",
            "⚖️",
            "Why a 95% signal does NOT always mean fast internet, and how each metric is calculated.",
            color=COLORS["warning"]
        )
        score_diff_table = (
            "Metric Name          │ Scale / Units │ What It Measures                                │ Why It Matters\n"
            "─────────────────────┼───────────────┼─────────────────────────────────────────────────┼────────────────────────────────────────\n"
            "1. Signal Strength   │ 0 - 100 %     │ Raw Physical Radio (RF) power received by your  │ Shows how 'loud' the router is. BUT a\n"
            "   (Antenna Power)   │ (-100 to -40) │ Wi-Fi antenna (P_rx in dBm).                    │ loud router on a congested channel is slow!\n"
            "─────────────────────┼───────────────┼─────────────────────────────────────────────────┼────────────────────────────────────────\n"
            "2. Link Quality      │ 0 - 100 Pts   │ Multi-factor fitness of the router connection:  │ The true measure of network speed.\n"
            "   (M-BFF Score)     │ Score         │ Signal (30) + Clean Channel (20) + 5G/6G (20)  │ 60% on clean 5GHz is 5x faster than\n"
            "                     │               │ + Wi-Fi Standard (20) + Security (10).          │ 95% on noisy 2.4GHz!\n"
            "─────────────────────┼───────────────┼─────────────────────────────────────────────────┼────────────────────────────────────────\n"
            "3. Optimization Level│ 0 - 100 %     │ Percentage of client-side Windows driver, NDIS, │ Confirms your PC's hardware and network\n"
            "   (System Tuned)    │ % Active      │ and TCP/IP kernel settings actively optimized.   │ stack are fully uncapped."
        )
        _code_box(c0, score_diff_table)

        example_box = (
            "REAL-WORLD SCENARIO COMPARISON:\n"
            "• Access Point A (Old 2.4GHz):  Signal = 95% (-52 dBm)  │ Overlap = 8 neighbors on Ch 6 │ Standard = Wi-Fi 4 (802.11n)\n"
            "  -> Link Quality (M-BFF) Score: 44 / 100  [Result: High signal, but severe packet loss, buffering & high ping!]\n\n"
            "• Access Point B (Modern 5GHz): Signal = 65% (-68 dBm)  │ Overlap = 0 neighbors on Ch 36│ Standard = Wi-Fi 6 (802.11ax)\n"
            "  -> Link Quality (M-BFF) Score: 88 / 100  [Result: Lower signal, but 5x higher bandwidth, zero jitter & 100% throughput!]\n\n"
            "WiFi Optimizer guides your adapter toward the highest Link Quality (M-BFF), not just blind signal loudness."
        )
        _code_box(c0, example_box)

        # ── 1. Pipeline Architecture ──────────────────────────────────────────
        c1 = _help_card(
            "1. System Architecture: 5-Stage Cross-Layer Pipeline",
            "🗺️",
            "Bridging physical radio telemetry, MAC contention, NDIS drivers, and kernel TCP/IP.",
            color=COLORS["accent"]
        )
        diagram_text = (
            "┌────────────────────────────────────────────────────────────────────────┐\n"
            "│                      1. RF & PHY TELEMETRY ENGINE                      │\n"
            "│ • Hardware dBm Query via wlanapi.dll (WlanGetNetworkBssList -> lRssi) │\n"
            "│ • Free-Space Reference Loss: PL(1m) = 20*log10(f_MHz) - 27.55           │\n"
            "│ • Multi-Exponent Range: d_n = 10 ^ ((P_tx - P_rx - PL(1m)) / (10*n))   │\n"
            "│ • 22 MHz Spectral Mask Overlap: w_ov(Δch) = max(0, 1 - |Δch|*5/22)     │\n"
            "└───────────────────────────────────┬────────────────────────────────────┘\n"
            "                                    │\n"
            "                                    ▼\n"
            "┌────────────────────────────────────────────────────────────────────────┐\n"
            "│            2. MULTI-OBJECTIVE BSSID FITNESS FUNCTION (M-BFF)           │\n"
            "│   S(N) = f_sig(P_rx) + f_cong(C) + f_band(B, P_rx) +                   │\n"
            "│          f_phy(R) + f_sec(A)    ∈ [0, 100 Points]                      │\n"
            "│ • Continuous Sigmoid: f_sig = 30 / (1 + exp(-0.35 * (P_rx + 72.0)))    │\n"
            "│ • Security Policy Gating: Hard disqualification of Open & WEP networks │\n"
            "└───────────────────────────────────┬────────────────────────────────────┘\n"
            "                                    │\n"
            "                                    ▼\n"
            "┌────────────────────────────────────────────────────────────────────────┐\n"
            "│         3. DISCOVERY-VALIDATED NDIS DRIVER HARDWARE TUNING             │\n"
            "│ • Registry Discovery: Parses Ndi\\params enums & integer ranges         │\n"
            "│ • Inverted Enum Trap Handler: Realtek (0=Max) vs. Intel (100=Max)      │\n"
            "│ • Connection-PHY Gated Short Guard Interval (SGI applied to 11n/ac)    │\n"
            "│ • 802.1p Layer-2 User Priority VLAN Frame Tagging (*PriorityVLANTag=3) │\n"
            "└───────────────────────────────────┬────────────────────────────────────┘\n"
            "                                    │\n"
            "                                    ▼\n"
            "┌────────────────────────────────────────────────────────────────────────┐\n"
            "│         4. WINDOWS KERNEL TRANSPORT LAYER (TCP/IP) OPTIMIZATION        │\n"
            "│ • TCP Receive Window Auto-Tuning = Normal (BDP Scaling up to 16MB)     │\n"
            "│ • Receive Side Scaling (RSS) = Distributes IRQs across CPU Cores       │\n"
            "│ • Explicit Congestion Notification (ECN) = Prevents Bufferbloat Drops  │\n"
            "│ • Sub-10ms Anycast DNS (1.1.1.1 / 8.8.8.8) & Resolver Cache Flush      │\n"
            "└───────────────────────────────────┬────────────────────────────────────┘\n"
            "                                    │\n"
            "                                    ▼\n"
            "┌────────────────────────────────────────────────────────────────────────┐\n"
            "│                  5. CLOSED-LOOP LINK GOVERNOR                          │\n"
            "│ • EWMA Telemetry Smoothing: P_tilde(t) = 0.3*P(t) + 0.7*P_tilde(t-1)   │\n"
            "│ • Dual-Threshold Hysteresis: -75 dBm Fall (3 smp) / -70 dBm Rec (5 smp)│\n"
            "│ • Gradient Drop Fast-Trigger: ∇P <= -15 dB over <= 12s                 │\n"
            "│ • 100% Non-Destructive: Zero driver reloads or interface disconnects   │\n"
            "└────────────────────────────────────────────────────────────────────────┘"
        )
        _code_box(c1, diagram_text)

        # ── 2. RF Physics, RSSI & Propagation Math ────────────────────────────
        c2 = _help_card(
            "2. RF Radio Physics & Environmental Propagation Math",
            "📡",
            "Native hardware RSSI queries, Friis path loss, and multi-exponent distance intervals.",
            color=COLORS["accent"]
        )
        _formula_box(c2, "1. Native Hardware RSSI Query (Physical dBm)",
                     "wlanapi.dll -> WlanGetNetworkBssList() -> lRssi (signed 32-bit dBm)",
                     "Reads real calibrated RF power from the adapter transceiver (±1.5 dB uncertainty). When native query is unavailable, netsh percentage fallback uses: P_rx = (P / 2.0) - 100.0 with ±6.0 dB uncalibrated margin.")

        _formula_box(c2, "2. Free-Space 1-Meter Reference Path Loss (Friis Model)",
                     "FSPL_1m (dB) = 20 * log10(f_MHz) - 27.55\n"
                     "• 2437 MHz (2.4GHz Ch 6):  FSPL_1m = 40.19 dB\n"
                     "• 5180 MHz (5GHz Ch 36):   FSPL_1m = 46.74 dB\n"
                     "• 5975 MHz (6GHz Ch 5):    FSPL_1m = 47.98 dB",
                     "Derivation: FSPL = 20*log10(4*π*d / λ). At d = 1m and c = 3e8 m/s, constant reduces to 20*log10(4*π*10^6 / 3e8) = -27.55 dB.")

        _formula_box(c2, "3. Environmental Propagation Range Intervals (Inverted Log-Distance)",
                     "d_n (meters) = 10 ^ ((P_tx - P_rx - FSPL_1m) / (10 * n))\n"
                     "• n = 2.0 : Free-Space / Clear Line-of-Sight (LOS)\n"
                     "• n = 3.0 : Indoor Residential / Drywall Partitions\n"
                     "• n = 4.0 : Dense Obstructions / Reinforced Concrete & Brick Walls",
                     "Example: At 5180 MHz (5GHz) with router P_tx = 20 dBm and client P_rx = -65 dBm (Path Loss = 85 dB):\n"
                     "  -> Free Space (n=2.0): d = 81.9 m | Residential (n=3.0): d = 20.8 m | Dense Concrete (n=4.0): d = 8.2 m\n"
                     "Physical Note: Real indoor Wi-Fi experiences shadow fading; the optimizer displays environmental intervals rather than pseudo-precise single distances.")

        # ── 3. Spectral Overlap & Channel Congestion ──────────────────────────
        c3 = _help_card(
            "3. 22 MHz Spectral Mask Overlap & Co-Channel Interference (CCI)",
            "📻",
            "Why overlapping Wi-Fi channels destroy speeds, and how the overlap integral weights interference.",
            color=COLORS["accent2"]
        )
        _formula_box(c3, "22 MHz Overlap Integral Weight Formula",
                     "w_ov(Δch) = max( 0.0, round( 1.0 - (|Δch| * 5.0 MHz) / 22.0 MHz, 2 ) )\n"
                     "• Δch = 0 : w_ov = 1.00  (Co-Channel Contention - Direct CSMA/CA Backoff)\n"
                     "• Δch = 1 : w_ov = 0.77  (Severe Adjacent Channel Spillover)\n"
                     "• Δch = 2 : w_ov = 0.55  (High Adjacent Interference)\n"
                     "• Δch = 3 : w_ov = 0.32  (Moderate Channel Bleed)\n"
                     "• Δch = 4 : w_ov = 0.09  (Marginal Filter Rolloff Overlap)\n"
                     "• Δch >= 5: w_ov = 0.00  (Orthogonal / Zero Overlap, e.g. Ch 1, 6, 11)",
                     "Standard 802.11 DSSS/OFDM channels have a 22 MHz spectral mask with 5 MHz center-frequency spacing. Non-orthogonal channels corrupt packet preambles, triggering frame retransmissions.")

        _formula_box(c3, "Effective Channel Congestion Score",
                     "C_eff = Σ [ w_ov(Δch_j) * w_sig(P_rx,j) ] + 5.0 * u\n"
                     "f_cong(C) = max( 0.0, 20.0 - 5.0 * C_eff )   ∈ [0, 20 Points]",
                     "Where u ∈ [0, 1] is the BSS Load channel utilization factor from IEEE 802.11 QBSS elements, and w_sig weights stronger interferers higher.")

        # ── 4. The Multi-Objective Fitness Function (M-BFF) ───────────────────
        c4 = _help_card(
            "4. The Multi-Objective BSSID Fitness Function (M-BFF)",
            "📊",
            "Mathematical formulation scoring every candidate access point across 5 objective functions.",
            color=COLORS["warning"]
        )
        _formula_box(c4, "Master Objective Fitness Formula",
                     "S(N) = f_sig(P_rx) + f_cong(C) + f_band(B, P_rx) + f_phy(R) + f_sec(A)   ∈ [0, 100 Pts]",
                     "Evaluates physical signal quality, spectral cleanliness, multi-gigabit band capacity, modulation standard, and encryption cipher.")

        _formula_box(c4, "1. Calibrated Continuous Sigmoid Signal Function (0 - 30 Pts)",
                     "f_sig(P_rx) = 30.0 / ( 1.0 + exp( -0.35 * (P_rx + 72.0) ) )\n"
                     "• P_rx >= -55 dBm : f_sig = 29.9 Pts (Near-field saturation, maximum SNR)\n"
                     "• P_rx = -72 dBm  : f_sig = 15.0 Pts (Calibrated inflection point)\n"
                     "• P_rx = -75 dBm  : f_sig = 7.9 Pts  (Rapid decline; modulation rate drops)\n"
                     "• P_rx <= -90 dBm : f_sig < 0.1 Pts  (Receiver sensitivity threshold)",
                     "Continuous and strictly monotonic: eliminates arbitrary tier jumps while penalizing weak links exponentially.")

        _formula_box(c4, "2. Band Capacity with Link Margin Attenuation Gate (0 - 20 Pts)",
                     "f_band(B, P_rx) = B * [ 1.0 / ( 1.0 + exp( -0.25 * (P_rx + 80.0) ) ) ]\n"
                     "• 6.0 GHz Band : Base B = 20.0 Pts (Ultra-wide 160/320 MHz channels)\n"
                     "• 5.0 GHz Band : Base B = 18.0 Pts (Clean 80 MHz channel bonding)\n"
                     "• 2.4 GHz Band : Base B = 8.0 Pts  (Narrow 20 MHz, high penetration)",
                     "Dynamic Attenuation: When signal drops below -80 dBm, 5GHz attenuates, allowing 2.4 GHz long-range propagation to win naturally.")

        _formula_box(c4, "3. PHY Modulation Generation (0 - 20 Pts)",
                     "• 802.11be (Wi-Fi 7, 4096-QAM, 320 MHz, MLO) : 20.0 Pts\n"
                     "• 802.11ax (Wi-Fi 6/6E, 1024-QAM, OFDMA)      : 18.0 Pts\n"
                     "• 802.11ac (Wi-Fi 5, 256-QAM, 80 MHz)         : 14.0 Pts\n"
                     "• 802.11n  (Wi-Fi 4, 64-QAM, MIMO)            : 9.0 Pts\n"
                     "• 802.11a/b/g (Legacy Modulation)             : 3.0 Pts",
                     "Higher modulation standards provide denser subcarrier packing and lower latency.")

        _formula_box(c4, "4. Security Policy Gating & Cipher Evaluation",
                     "• WPA3-SAE (Simultaneous Authentication of Equals) : 10.0 Pts\n"
                     "• WPA2-AES / CCMP (Counter Mode CBC-MAC)           : 8.0 Pts\n"
                     "• WPA2-TKIP (Deprecated Stream Cipher)             : 3.0 Pts\n"
                     "• Open (Plaintext) & WEP (Broken RC4 Cipher)       : HARD DISQUALIFICATION\n"
                     "  -> is_eligible = False (Excluded from candidate selection by security policy)",
                     "Saved Profile Filter: The optimizer restricts autonomous roaming to networks in your configured Windows profiles (require_saved_profile=True), preventing rogue AP spoofing.")

        # ── 5. NDIS Driver Hardware Tuning & Inverted Enum Trap ───────────────
        c5 = _help_card(
            "5. Discovery-Validated NDIS Driver Tuning & The Inverted Enum Trap",
            "⚙️",
            "Why generic Wi-Fi boosters fail: understanding real Intel vs. Realtek driver registry encodings.",
            color=COLORS["hero_btn"]
        )
        enum_table = (
            "Hardware Intent      │ Intel (AX200, AX201, AX210, BE200) │ Realtek (RTL8821CE, RTL8822BE, RTL8852AE)\n"
            "─────────────────────┼────────────────────────────────────┼──────────────────────────────────────────\n"
            "Transmit Power       │ IbssTxPower: '100' = Highest       │ TxPwrLevel: '0' = Highest ('4' is Lowest!)\n"
            "Roaming Aggressive   │ RoamAggressiveness: '4' = Highest  │ RegROAMSensitiveLevel: '65' = High ('127'=Disable)\n"
            "Preferred Band       │ RoamingPreferredBandType: '2' = 5G │ PreferBand: '2' = 5G first\n"
            "MIMO Power Save      │ MIMOPowerSaveMode: '3' = No SMPS   │ (Setting '0' on Intel enables Auto SMPS!)\n"
            "Sleep on Disconnect  │ *DeviceSleepOnDisconnect: '0'=Off  │ WakeOnDisconnect: '1' = Disabled ('0'=Enabled!)"
        )
        _code_box(c5, enum_table)

        _formula_box(c5, "Connection-PHY Gated Short Guard Interval (SGI)",
                     "Gated strictly by active connection PHY:\n"
                     "• Active 802.11n or 802.11ac : Enables 400ns SGI (+11% symbol throughput gain)\n"
                     "• Active 802.11ax or 802.11be: SKIPPED! (Wi-Fi 6 uses dynamic 0.8µs/1.6µs HE guards)",
                     "Prevents overriding Wi-Fi 6 OFDMA guard intervals while boosting legacy 802.11ac links.")

        _formula_box(c5, "802.1p Layer-2 User Priority VLAN QoS Tagging",
                     "NDIS Keyword: *PriorityVLANTag = 3 (Packet Priority and VLAN Enabled)\n"
                     "Sets 3-bit Priority Code Point (PCP) in 802.1Q tags to prioritize gaming & media frames.",
                     "Clarification: Does not modify router-side WMM contention directly; prioritizes frames inside local Windows network queues.")

        # ── 6. Dynamic Feedback Governor & Anti-Flapping State Machine ────────
        c6 = _help_card(
            "6. Dynamic Closed-Loop Link Governor & Anti-Flapping Engine",
            "🔄",
            "Preventing hysteresis ping spikes and border thrashing during active gameplay or video calls.",
            color=COLORS["success"]
        )
        _formula_box(c6, "1. EWMA Telemetry Smoothing Filter",
                     "P_tilde(t) = α * P_rx(t) + (1.0 - α) * P_tilde(t-1)   [Smoothing factor α = 0.3]",
                     "Suppresses short-term multipath fading dips without delaying sustained degradation detection.")

        _formula_box(c6, "2. Asymmetric Dual-Threshold Hysteresis State Machine",
                     "• HEALTHY -> DEGRADED : Requires 3 consecutive samples < -75 dBm\n"
                     "• DEGRADED -> HEALTHY : Requires 5 consecutive samples >= -70 dBm (5 dB gap)\n"
                     "• Gradient Drop Rule  : ∇P = P_rx(t) - P_rx(t0) <= -15 dB over <= 12 seconds\n"
                     "  -> Immediately triggers DEGRADED state without waiting for 3 samples!\n"
                     "• Roaming Cooldown    : Enforces 30-second cooldown between handoffs to prevent thrashing.",
                     "100% Non-Destructive Guarantee: The Governor never performs mid-session driver reloads, adapter disables, or TCP resets while streams are active.")

        # ── 7. Windows Kernel TCP/IP Optimization ─────────────────────────────
        c7 = _help_card(
            "7. Windows Kernel TCP/IP Transport Layer Optimization",
            "🌐",
            "Uncapping the operating system network pipe to eliminate bufferbloat and DPC queueing delays.",
            color=COLORS["accent"]
        )
        _formula_box(c7, "Dynamic Window Auto-Tuning based on Bandwidth-Delay Product",
                     "BDP (Bytes) = ( Bandwidth_bps * RTT_sec ) / 8\n"
                     "netsh int tcp set global autotuninglevel=normal",
                     "Scales TCP Receive Windows dynamically up to 16 MB. At 1 Gbps with 40ms RTT, BDP = 5 MB. Default static Windows buffers truncate throughput at 64 KB!")

        _formula_box(c7, "Multi-Core Receive Side Scaling (RSS)",
                     "netsh int tcp set global rss=enabled",
                     "Distributes packet interrupt servicing across multiple CPU cores, preventing Core 0 thread saturation during multi-gigabit downloads.")

        _formula_box(c7, "Explicit Congestion Notification (ECN, RFC 3168)",
                     "netsh int tcp set global ecncapability=enabled",
                     "Instructs intervening routers to mark Congestion Experienced (CE) bits instead of dropping packets, ending bufferbloat latency spikes.")

        # ── 8. Safety, Reversibility & Dry-Run Preview ────────────────────────
        c8 = _help_card(
            "8. Safety, Reversibility & Dry-Run Preview Architecture",
            "🛡️",
            "Complete transparency: preview before changing, and restore defaults with one click.",
            color="#A78BFA"
        )
        safety_text = (
            "• Read-Only Preview (Dry-Run): Toggle 'Preview Changes' to inspect exact Current -> Proposed values before anything is written.\n"
            "• Automatic Snapshot Backup: Before modifying any setting, the optimizer writes adapter_rollback_snapshot.json saving original driver and TCP values.\n"
            "• One-Click Rollback / Restore: Reverts all modified keys to OEM defaults and removes newly created registry parameters.\n"
            "• Discovery Validation: Properties not declared in your driver's 'Ndi\\params' database are skipped—never guessed.\n"
            "• Target Interface Detection: Automatically identifies your active wireless interface name (fixes issues on non-English Windows editions)."
        )
        _code_box(c8, safety_text)

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
            connected_score = None
            if adapter:
                for sc in scored:
                    if sc.network.ssid == adapter.ssid:
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

        # Capture BEFORE state
        from core.wifi_scanner import get_connected_network
        self._before_snapshot = get_connected_network()

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
            preview = bool(self._preview_var.get())
            result = run_optimization(
                profile_key=self._profile_var.get(),
                adapter_name=getattr(self, '_adapter_var',
                                     type('_', (), {'get': lambda s: 'Wi-Fi'})()).get(),
                progress_callback=progress,
                preview=preview,
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

        # ── Compute improvement score ─────────────────────────────────────────
        before = self._before_snapshot
        after = self._after_snapshot or before

        # Optimization Level: percentage of recommended tweaks successfully applied
        applied_count = len(result.settings_applied)
        failed_count = len(result.settings_failed)
        total_tweaks = max(1, applied_count + failed_count)
        opt_level = int((applied_count / total_tweaks) * 100)

        self._score_badge.animate_to(opt_level)

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

        # Failed (smaller) — use grid to stay consistent with applied chips
        applied_rows = (count + cols - 1) // cols if count > 0 else 0
        for i, s in enumerate(result.settings_failed):
            chip = ctk.CTkFrame(self._applied_frame, fg_color=COLORS["surface2"],
                                 corner_radius=6)
            chip.grid(row=applied_rows + i // cols, column=i % cols, padx=4, pady=3, sticky="ew")
            self._applied_frame.columnconfigure(i % cols, weight=1)
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
        if "quality" in self._chips:
            self._chips["quality"].update(snap.quality)
        if "down" in self._chips:
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
