#!/usr/bin/env python3
"""
OPENCLUTCH TUI — Clutch v1.77
Multi-agent terminal dashboard. Runs on any machine with Python 3 + Ollama.

Usage:
    python3 tui.py
    python3 tui.py --model qwen3:8b
    python3 tui.py --host http://localhost:11434
"""

import argparse, curses, json, os, subprocess, sys, textwrap, threading, time
import urllib.request, urllib.parse

# ── Config ────────────────────────────────────────────────────────────────────

OLLAMA_HOST   = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = "qwen3:8b"
VERSION       = "1.77"

AGENTS = [
    ("researcher", "Researcher",  "Web research & reports"),
    ("browser",    "Browser",     "Browser automation & scraping"),
    ("twitter",    "Twitter",     "Social posting & monitoring"),
    ("clip",       "Clip",        "Content clipping & summaries"),
    ("lead",       "Lead",        "Lead generation & outreach"),
    ("collector",  "Collector",   "Data collection & aggregation"),
    ("predictor",  "Predictor",   "Forecasting & trend analysis"),
    ("trader",     "Trader",      "Market monitoring & signals"),
]

SYSTEM_PROMPTS = {
    "researcher": (
        "You are the Researcher agent in OPENCLUTCH. You search the web, "
        "synthesize findings, and deliver concise research reports. "
        "Be specific, cite what you found, lead with the answer."
    ),
    "browser": (
        "You are the Browser agent in OPENCLUTCH. You automate web tasks — "
        "scraping, form filling, navigation. Describe steps clearly and flag "
        "anything requiring human interaction."
    ),
    "twitter": (
        "You are the Twitter agent in OPENCLUTCH managing @OPENCLUTCH2. "
        "Voice: provocative builder. Topics: local AI, autonomous agents, "
        "open source inference, decentralized compute. "
        "Write tweets under 280 chars. No hashtag spam. No fluff."
    ),
    "clip": (
        "You are the Clip agent in OPENCLUTCH. You extract key information "
        "from URLs or pasted content and return clean, structured summaries."
    ),
    "lead": (
        "You are the Lead agent in OPENCLUTCH. You help identify, qualify, "
        "and research potential leads or contacts. Be direct and data-driven."
    ),
    "collector": (
        "You are the Collector agent in OPENCLUTCH. You gather, organize, "
        "and structure data from any source the user provides."
    ),
    "predictor": (
        "You are the Predictor agent in OPENCLUTCH. You analyze trends and "
        "make data-driven forecasts. Show your reasoning, flag uncertainty."
    ),
    "trader": (
        "You are the Trader agent in OPENCLUTCH. You monitor markets, "
        "identify signals, and provide analysis. Never give financial advice "
        "— provide analysis only."
    ),
}

DEFAULT_SYSTEM = (
    "You are Clutch, the OPENCLUTCH assistant. "
    "Built for builders. Short answers, direct tone, no fluff."
)

# ── Ollama ────────────────────────────────────────────────────────────────────

def infer(model, messages, callback=None):
    """
    Stream Ollama chat. Calls callback(token) for each chunk if provided,
    otherwise returns full response string.
    """
    payload = {"model": model, "messages": messages, "stream": True}
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    full = []
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    token = d.get("message", {}).get("content", "")
                    if token:
                        full.append(token)
                        if callback:
                            callback(token)
                except Exception:
                    pass
        return "".join(full).strip()
    except Exception as e:
        err = f"[Error: {e}]"
        if callback:
            callback(err)
        return err


def get_openfang_agents():
    """Return list of running OpenFang agents [{id, name, state}]."""
    try:
        r = subprocess.run(
            ["openfang", "agent", "list"],
            capture_output=True, text=True, timeout=5,
        )
        agents = []
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and len(parts[0]) == 36 and parts[0].count("-") == 4:
                agents.append({"id": parts[0], "name": parts[1], "state": parts[2]})
        return agents
    except Exception:
        return []


# ── TUI ───────────────────────────────────────────────────────────────────────

class ClutchTUI:
    SIDEBAR_W = 22

    def __init__(self, model):
        self.model          = model
        self.active         = 0          # selected agent index
        self.histories      = {k: [] for k, _, _ in AGENTS}
        self.input_buf      = ""
        self.thinking       = False
        self.of_agents      = []         # openfang agent list cache
        self.scroll_offset  = 0
        self._lock          = threading.Lock()
        self._stream_buf    = ""
        self._dirty         = True

    # ── drawing ───────────────────────────────────────────────────────────────

    def _init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN,    -1)   # header / accent
        curses.init_pair(2, curses.COLOR_GREEN,   -1)   # active agent / you
        curses.init_pair(3, curses.COLOR_YELLOW,  -1)   # agent response
        curses.init_pair(4, curses.COLOR_WHITE,   -1)   # normal text
        curses.init_pair(5, curses.COLOR_BLACK,   curses.COLOR_CYAN)   # selected row
        curses.init_pair(6, curses.COLOR_RED,     -1)   # error / thinking

    def draw(self, stdscr):
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        sw   = self.SIDEBAR_W
        cw   = w - sw - 1   # chat width

        self._draw_header(stdscr, w)
        self._draw_sidebar(stdscr, h, sw)
        stdscr.vline(1, sw, curses.ACS_VLINE, h - 3)
        self._draw_chat(stdscr, h, sw + 1, cw)
        self._draw_input(stdscr, h, w)
        stdscr.refresh()

    def _draw_header(self, stdscr, w):
        title = f" OPENCLUTCH  ·  Clutch v{VERSION}  ·  Local AI "
        pad   = " " * (w - len(title))
        try:
            stdscr.addstr(0, 0, (title + pad)[:w],
                          curses.color_pair(5) | curses.A_BOLD)
        except curses.error:
            pass

    def _draw_sidebar(self, stdscr, h, sw):
        try:
            stdscr.addstr(1, 1, "AGENTS", curses.color_pair(1) | curses.A_BOLD)
        except curses.error:
            pass

        of_names = {a["name"].lower() for a in self.of_agents if a["state"] == "Running"}

        for i, (key, name, desc) in enumerate(AGENTS):
            row = 2 + i
            if row >= h - 2:
                break
            running = key in of_names
            dot     = "●" if running else "○"
            label   = f" {dot} {i+1}. {name[:12]}"
            try:
                if i == self.active:
                    stdscr.addstr(row, 0, label.ljust(sw),
                                  curses.color_pair(5) | curses.A_BOLD)
                elif running:
                    stdscr.addstr(row, 0, label.ljust(sw),
                                  curses.color_pair(2))
                else:
                    stdscr.addstr(row, 0, label.ljust(sw),
                                  curses.color_pair(4))
            except curses.error:
                pass

        # shortcuts
        shortcuts = " [1-8]sel [R]fresh [Q]quit"
        try:
            stdscr.addstr(h - 2, 0, shortcuts[:sw], curses.color_pair(1))
        except curses.error:
            pass

    def _draw_chat(self, stdscr, h, cx, cw):
        key, name, desc = AGENTS[self.active]
        header = f" {name.upper()} — {desc} "
        try:
            stdscr.addstr(1, cx, header[:cw], curses.color_pair(1) | curses.A_BOLD)
        except curses.error:
            pass

        history  = self.histories[key]
        chat_h   = h - 4   # rows available for chat
        lines    = []

        for msg in history:
            role    = msg["role"]
            content = msg["content"]
            if role == "user":
                prefix = "You: "
                color  = curses.color_pair(2)
            elif role == "assistant":
                prefix = f"{name}: "
                color  = curses.color_pair(3)
            else:
                continue
            wrapped = textwrap.wrap(prefix + content, cw - 2)
            for j, wl in enumerate(wrapped):
                lines.append((wl if j > 0 else wl, color))
            lines.append(("", curses.color_pair(4)))   # blank between messages

        # streaming partial
        with self._lock:
            partial = self._stream_buf
        if partial:
            prefix  = f"{name}: "
            wrapped = textwrap.wrap(prefix + partial + "▌", cw - 2)
            for j, wl in enumerate(wrapped):
                lines.append((wl, curses.color_pair(6)))

        # thinking indicator
        if self.thinking and not partial:
            lines.append((f"{name}: thinking...", curses.color_pair(6)))

        # scroll
        max_scroll = max(0, len(lines) - chat_h)
        self.scroll_offset = min(self.scroll_offset, max_scroll)
        visible = lines[self.scroll_offset: self.scroll_offset + chat_h]

        for i, (line, color) in enumerate(visible):
            row = 2 + i
            if row >= h - 2:
                break
            try:
                stdscr.addstr(row, cx + 1, line[:cw - 1], color)
            except curses.error:
                pass

    def _draw_input(self, stdscr, h, w):
        prompt  = "> "
        display = (prompt + self.input_buf)[-(w - 2):]
        try:
            stdscr.addstr(h - 1, 0, display.ljust(w - 1),
                          curses.color_pair(4) | curses.A_BOLD)
            # position cursor
            cursor_x = min(len(display), w - 2)
            stdscr.move(h - 1, cursor_x)
        except curses.error:
            pass

    # ── input handling ────────────────────────────────────────────────────────

    def _switch_agent(self, idx):
        if 0 <= idx < len(AGENTS):
            self.active        = idx
            self.scroll_offset = 0

    def _send(self):
        text = self.input_buf.strip()
        if not text or self.thinking:
            return
        self.input_buf = ""

        key, name, _ = AGENTS[self.active]
        system        = SYSTEM_PROMPTS.get(key, DEFAULT_SYSTEM)
        self.histories[key].append({"role": "user", "content": text})

        # scroll to bottom
        self.scroll_offset = 999999

        messages = [{"role": "system", "content": system}] + self.histories[key]
        self.thinking = True

        def _worker():
            with self._lock:
                self._stream_buf = ""

            def _on_token(tok):
                with self._lock:
                    self._stream_buf += tok

            reply = infer(self.model, messages, callback=_on_token)

            with self._lock:
                self._stream_buf = ""
            self.histories[key].append({"role": "assistant", "content": reply})
            self.thinking      = False
            self.scroll_offset = 999999

        threading.Thread(target=_worker, daemon=True).start()

    def _refresh_agents(self):
        def _worker():
            self.of_agents = get_openfang_agents()
        threading.Thread(target=_worker, daemon=True).start()

    # ── main loop ─────────────────────────────────────────────────────────────

    def run(self, stdscr):
        self._init_colors()
        curses.curs_set(1)
        stdscr.nodelay(True)
        stdscr.timeout(100)

        self._refresh_agents()

        refresh_tick = 0
        while True:
            self.draw(stdscr)

            # auto-refresh agent list every 10s
            refresh_tick += 1
            if refresh_tick >= 100:
                refresh_tick = 0
                self._refresh_agents()

            key = stdscr.getch()
            if key == -1:
                continue

            h, w = stdscr.getmaxyx()

            if key in (ord('q'), ord('Q')):
                break

            elif key in (ord('r'), ord('R')):
                self._refresh_agents()

            elif ord('1') <= key <= ord('8'):
                self._switch_agent(key - ord('1'))

            elif key in (curses.KEY_UP,):
                self.scroll_offset = max(0, self.scroll_offset - 1)

            elif key in (curses.KEY_DOWN,):
                self.scroll_offset += 1

            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.input_buf = self.input_buf[:-1]

            elif key in (curses.KEY_ENTER, ord('\n'), ord('\r')):
                self._send()

            elif 32 <= key <= 126:
                self.input_buf += chr(key)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    global OLLAMA_HOST

    parser = argparse.ArgumentParser(description=f"OPENCLUTCH TUI v{VERSION}")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--host",  default=OLLAMA_HOST)
    args = parser.parse_args()

    OLLAMA_HOST = args.host

    tui = ClutchTUI(model=args.model)
    try:
        curses.wrapper(tui.run)
    except KeyboardInterrupt:
        pass
    print(f"\n  OPENCLUTCH TUI closed. Agents still running.\n")


if __name__ == "__main__":
    main()
