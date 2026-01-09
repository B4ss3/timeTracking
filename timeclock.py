import csv
import json
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# Optional tray support
TRAY_AVAILABLE = True
try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    TRAY_AVAILABLE = False

APP_DIR = Path.home() / ".timeclock"
DATA_FILE = APP_DIR / "sessions.json"


def now_local() -> datetime:
    return datetime.now().astimezone()


def dt_to_iso(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def iso_to_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def fmt_duration(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def start_of_today_local() -> datetime:
    n = now_local()
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass
class Session:
    start_iso: str
    end_iso: Optional[str]
    note: str

    def start_dt(self) -> datetime:
        return iso_to_dt(self.start_iso)

    def end_dt(self) -> Optional[datetime]:
        return iso_to_dt(self.end_iso) if self.end_iso else None

    def is_running(self) -> bool:
        return self.end_iso is None

    def duration_seconds(self) -> int:
        start = self.start_dt()
        end = self.end_dt() or now_local()
        return int((end - start).total_seconds())


def ensure_storage() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        DATA_FILE.write_text(json.dumps({"sessions": []}, indent=2), encoding="utf-8")


def load_sessions() -> List[Session]:
    ensure_storage()
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return [Session(**s) for s in raw.get("sessions", [])]


def save_sessions(sessions: List[Session]) -> None:
    ensure_storage()
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"sessions": [asdict(s) for s in sessions]}, indent=2), encoding="utf-8")
    os.replace(tmp, DATA_FILE)


def make_tray_image(size: int = 64) -> "Image.Image":
    # Simple clock icon
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size // 8
    d.ellipse((pad, pad, size - pad, size - pad), outline=(30, 30, 30, 255), width=max(2, size // 16))
    cx, cy = size // 2, size // 2
    # hands
    d.line((cx, cy, cx, pad + 6), fill=(30, 30, 30, 255), width=max(2, size // 20))
    d.line((cx, cy, size - pad - 8, cy + 6), fill=(30, 30, 30, 255), width=max(2, size // 22))
    d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=(30, 30, 30, 255))
    return img


class TimeClockApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Time Clock")
        self.geometry("860x540")

        self.sessions: List[Session] = load_sessions()
        self._quitting = False
        self._hidden = False

        # Tray
        self.tray_icon = None
        self.tray_thread = None

        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")

        self.toggle_btn = ttk.Button(top, text="Start", command=self.toggle)
        self.toggle_btn.pack(side="left")

        ttk.Label(top, text="Note:").pack(side="left", padx=(12, 6))
        self.note_var = tk.StringVar()
        self.note_entry = ttk.Entry(top, textvariable=self.note_var, width=42)
        self.note_entry.pack(side="left", fill="x", expand=True)

        ttk.Button(top, text="Export CSV", command=self.export_csv).pack(side="left", padx=(12, 6))
        ttk.Button(top, text="Delete Selected", command=self.delete_selected).pack(side="left", padx=6)
        ttk.Button(top, text="Open Data File", command=self.open_data_file_hint).pack(side="left", padx=6)

        mid = ttk.Frame(self, padding=(12, 0))
        mid.pack(fill="x")

        self.status_var = tk.StringVar(value="Not running")
        self.today_total_var = tk.StringVar(value="0:00:00")
        self.all_total_var = tk.StringVar(value="0:00:00")

        ttk.Label(mid, textvariable=self.status_var, font=("TkDefaultFont", 16, "bold")).pack(anchor="w", pady=(8, 2))
        ttk.Label(mid, text="Today total:").pack(side="left", padx=(0, 6))
        ttk.Label(mid, textvariable=self.today_total_var).pack(side="left")
        ttk.Label(mid, text="   All-time total:").pack(side="left", padx=(18, 6))
        ttk.Label(mid, textvariable=self.all_total_var).pack(side="left")

        table_frame = ttk.Frame(self, padding=12)
        table_frame.pack(fill="both", expand=True)

        cols = ("start", "end", "dur", "note")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="extended")
        for c, title in [("start", "Start"), ("end", "End"), ("dur", "Duration"), ("note", "Note")]:
            self.tree.heading(c, text=title)

        self.tree.column("start", width=190, anchor="w")
        self.tree.column("end", width=190, anchor="w")
        self.tree.column("dur", width=90, anchor="w")
        self.tree.column("note", width=420, anchor="w")

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # Theme
        try:
            style = ttk.Style()
            if "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass

        # Close = hide to tray
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.refresh()
        self.after(1000, self.tick)

        # Start tray icon
        if TRAY_AVAILABLE:
            self.start_tray()
        else:
            # Not fatal; app still works.
            pass

    def running_session_index(self) -> Optional[int]:
        for i in range(len(self.sessions) - 1, -1, -1):
            if self.sessions[i].is_running():
                return i
        return None

    def is_running(self) -> bool:
        return self.running_session_index() is not None

    def toggle(self) -> None:
        idx = self.running_session_index()
        if idx is None:
            note = self.note_var.get().strip()
            self.sessions.append(Session(start_iso=dt_to_iso(now_local()), end_iso=None, note=note))
            self.note_var.set("")
        else:
            self.sessions[idx].end_iso = dt_to_iso(now_local())

        save_sessions(self.sessions)
        self.refresh()
        self.update_tray_menu()

    def delete_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        if not messagebox.askyesno("Delete", f"Delete {len(sel)} selected session(s)?"):
            return

        indices = sorted((int(item.split(":")[1]) for item in sel), reverse=True)
        for i in indices:
            if 0 <= i < len(self.sessions):
                del self.sessions[i]

        save_sessions(self.sessions)
        self.refresh()
        self.update_tray_menu()

    def export_csv(self) -> None:
        default_name = f"timeclock_{now_local().strftime('%Y%m%d_%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            title="Export CSV",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if not path:
            return

        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["start", "end", "duration_seconds", "note"])
            for s in self.sessions:
                start = s.start_iso
                end = s.end_iso or ""
                dur = s.duration_seconds() if s.end_iso else ""  # leave blank if still running
                w.writerow([start, end, dur, s.note])

        messagebox.showinfo("Exported", f"Saved:\n{path}")

    def open_data_file_hint(self) -> None:
        messagebox.showinfo(
            "Data file location",
            f"Your sessions are saved here:\n\n{DATA_FILE}\n\n(You can open it with any text editor.)"
        )

    def compute_totals(self) -> tuple:
        # Proper “overlap with today” counting
        all_sec = 0
        today_sec = 0

        today_start = start_of_today_local()
        tomorrow_start = today_start + timedelta(days=1)

        for s in self.sessions:
            start = s.start_dt()
            end = s.end_dt() or now_local()

            dur = int((end - start).total_seconds())
            if dur > 0:
                all_sec += dur

            overlap_start = max(start, today_start)
            overlap_end = min(end, tomorrow_start)
            overlap = int((overlap_end - overlap_start).total_seconds())
            if overlap > 0:
                today_sec += overlap

        return today_sec, all_sec

    def refresh(self) -> None:
        idx = self.running_session_index()
        if idx is None:
            self.toggle_btn.config(text="Start")
            self.status_var.set("Not running")
        else:
            self.toggle_btn.config(text="Stop")
            s = self.sessions[idx]
            self.status_var.set(f"Running… ({fmt_duration(s.duration_seconds())})")

        today_sec, all_sec = self.compute_totals()
        self.today_total_var.set(fmt_duration(today_sec))
        self.all_total_var.set(fmt_duration(all_sec))

        for item in self.tree.get_children():
            self.tree.delete(item)

        for i, s in enumerate(self.sessions):
            start = s.start_dt().strftime("%Y-%m-%d %H:%M:%S")
            end_dt = s.end_dt()
            end = end_dt.strftime("%Y-%m-%d %H:%M:%S") if end_dt else "—"
            dur = fmt_duration(s.duration_seconds())
            self.tree.insert("", "end", iid=f"row:{i}", values=(start, end, dur, s.note))

    def tick(self) -> None:
        if self.running_session_index() is not None:
            self.refresh()
        self.after(1000, self.tick)

    # ---------- Tray integration ----------
    def start_tray(self) -> None:
        if self.tray_icon is not None:
            return

        image = make_tray_image(64)

        def do_toggle(_icon, _item):
            self.after(0, self.toggle)

        def do_open(_icon, _item):
            # Always show + focus (good behavior for double-click)
            self.after(0, self.show_from_tray)

        def do_hide(_icon, _item):
            self.after(0, self.hide_to_tray)

        def do_quit(_icon, _item):
            self.after(0, self.quit_app)

        def label_start_stop(_item):
            return "Stop" if self.is_running() else "Start"

        menu = pystray.Menu(
            pystray.MenuItem("Open", do_open, default=True),   # <-- DOUBLE-CLICK runs this
            pystray.MenuItem(label_start_stop, do_toggle),
            pystray.MenuItem("Hide", do_hide),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", do_quit),
        )

        self.tray_icon = pystray.Icon("TimeClock", image, "Time Clock", menu)

        def run_tray():
            try:
                self.tray_icon.run()
            except Exception:
                pass

        self.tray_thread = threading.Thread(target=run_tray, daemon=True)
        self.tray_thread.start()

    def update_tray_menu(self) -> None:
        try:
            if self.tray_icon:
                self.tray_icon.update_menu()
        except Exception:
            pass

    def hide_to_tray(self) -> None:
        self.withdraw()
        self._hidden = True
        self.update_tray_menu()

    def show_from_tray(self) -> None:
        self.deiconify()
        self._hidden = False
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass
        self.update_tray_menu()

    def toggle_visibility(self) -> None:
        if self._hidden:
            self.show_from_tray()
        else:
            self.hide_to_tray()

    def quit_app(self) -> None:
        self._quitting = True
        # If a session is running, stop it now and persist.
        idx = self.running_session_index()
        if idx is not None:
            self.sessions[idx].end_iso = dt_to_iso(now_local())
            try:
                save_sessions(self.sessions)
            except Exception:
                pass

        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        self.destroy()

    def on_close(self) -> None:
        # Clicking X hides to tray (unless tray isn't available)
        if TRAY_AVAILABLE and self.tray_icon and not self._quitting:
            self.hide_to_tray()
        else:
            self.quit_app()


if __name__ == "__main__":
    app = TimeClockApp()
    app.mainloop()
