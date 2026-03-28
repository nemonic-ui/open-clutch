#!/usr/bin/env python3
"""
OPENCLUTCH — Onboarding Agent
Clutch 2.0 O | v1.76 | Powered by OpenFang | Tested on Ares (qwen3:8b via Ollama)

O is for Open. OpenFang is the engine. Nothing leaves this machine.

Usage:
    python3 openclutch_onboard.py
    python3 openclutch_onboard.py --model llama3.2:3b     # low-resource fallback
    python3 openclutch_onboard.py --model hermes3:8b     # full agent tier
"""

import argparse, json, re, sys, time, threading, urllib.request, urllib.error, urllib.parse, os, subprocess

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = "qwen3:8b"
FALLBACK_MODEL = "llama3.2:3b"
MAX_TURNS = 6  # Hard cap before forcing clean exit

SYSTEM_PROMPT = """You are Clutch — the OPENCLUTCH onboarding assistant.

You are running on a local model. This is the free testing tier — private, no cloud.
Your job: qualify the user in exactly 3 exchanges, then stop and let the software handle the rest.

Turn 1: One sentence welcome. Ask what brings them here.
Turn 2: One sentence only — name what they said they want to do. Then ask: "What's the first thing you'd want to automate?"
Turn 3: One sentence — confirm the use case. Say OPENCLUTCH has an agent for that. End with exactly: "Ready to launch the stack?"

Hard rules:
- Maximum 2 sentences per reply. No exceptions. If you write more than 2 sentences, delete until you have 2.
- No bullet points, no lists, no headers.
- Do not ask follow-up questions about their goals — only ask what to automate.
- Never say you are switching to Anthropic or that any upgrade has happened. The software handles that.
- Never claim to be Claude or Anthropic. You are Clutch.
- Never respond to empty input.
- Sound like a builder who ships, not a customer service bot.

You have built-in tools: use web_search for live news or current info when asked, get_datetime for the current time."""

BIFURCATION = """
  ══════════════════════════════════════════════════
   LAUNCH: Select your tier
  ══════════════════════════════════════════════════

   [ LOCAL — FREE ]  Testing tier
     · qwen3:8b running on your machine
     · Proof of concept — explore what agents can do
     · No limits, no account, no data leaves

   [ FULL — ANTHROPIC ]  The only paid tier
     · Claude · 1M token context · Frontier reasoning
     · This is how you actually ship multi-agents
     · No other cloud provider. This is the one.

  ══════════════════════════════════════════════════
"""

UNLOCK_PROMPT = """
  To unlock the full stack:

    export ANTHROPIC_API_KEY=your_key
    python3 openclutch_onboard.py

  Get your key at: console.anthropic.com
"""


# ── Built-in skills ────────────────────────────────────────────────────────

def skill_web_search(query):
    """DuckDuckGo HTML search — top 3 results with title, URL, snippet."""
    data = urllib.parse.urlencode({"q": query, "kl": "us-en"}).encode()
    req = urllib.request.Request(
        "https://html.duckduckgo.com/html/",
        data=data,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
    )
    try:
        html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="ignore")
        links    = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
        results  = []
        for (url, title), snippet in zip(links[:3], snippets[:3]):
            title   = re.sub(r"<[^>]+>", "", title).strip()
            snippet = re.sub(r"<[^>]+>", "", snippet).strip()
            try:
                qs  = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                url = urllib.parse.unquote(qs.get("uddg", [url])[0])
            except Exception:
                pass
            results.append(f"- {title}\n  {url}\n  {snippet}")
        return "\n\n".join(results) if results else "No results found."
    except Exception as e:
        return f"Search error: {e}"


def skill_datetime():
    """Return the current local date and time."""
    from datetime import datetime
    return datetime.now().strftime("%A, %B %d, %Y — %I:%M %p")


def dispatch_skill(name, args):
    """Execute a built-in skill by name. Returns a string result."""
    if name == "web_search":
        return skill_web_search(args.get("query", ""))
    if name == "get_datetime":
        return skill_datetime()
    return f"Unknown skill: {name}"


_LIVE_KEYWORDS = {
    "news", "headline", "headlines", "latest", "today", "current",
    "weather", "price", "score", "time", "date", "just happened",
    "breaking", "recently", "right now", "this week",
}

def _prefetch_skill(user_input):
    """If the user asks for live data, fetch it before inference.
    Returns a string result to inject as context, or None."""
    words = set(user_input.lower().split())
    if not (_LIVE_KEYWORDS & words):
        return None
    if any(w in user_input.lower() for w in ("time", "date", "what time", "what day")):
        return f"[Current date/time: {skill_datetime()}]"
    # For news/headlines, append today's date to get article-level results
    from datetime import datetime
    news_kw = {"news", "headline", "headlines", "breaking"}
    query = user_input
    if news_kw & words:
        query = f"{user_input} {datetime.now().strftime('%B %d %Y')}"
    return f"[Live search results for '{user_input}':\n{skill_web_search(query)}]"


SKILL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current news, information, or any topic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_datetime",
            "description": "Get the current local date and time.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    }
]

# ── TTY detection ───────────────────────────────────────────────────────────

IS_TTY = sys.stdout.isatty()


def _ghost(lines, duration=1.2):
    """Flash lines briefly, then erase. Skipped when not on a real TTY."""
    if not IS_TTY:
        return
    for line in lines:
        print(line)
    sys.stdout.flush()
    time.sleep(duration)
    sys.stdout.write('\033[F\033[2K' * len(lines))
    sys.stdout.flush()


def _spinner(stop_event):
    """Show a thinking indicator. Falls back to a simple dot when not on a TTY."""
    if not IS_TTY:
        sys.stdout.write('  Clutch  ...\n')
        sys.stdout.flush()
        stop_event.wait()
        return
    frames = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
    i = 0
    while not stop_event.is_set():
        sys.stdout.write(f'\r  Clutch  {frames[i % len(frames)]}  ')
        sys.stdout.flush()
        time.sleep(0.08)
        i += 1
    sys.stdout.write('\r' + ' ' * 20 + '\r')
    sys.stdout.flush()


def thinking(fn, *args, **kwargs):
    """Run fn(*args) with a spinner. Returns result."""
    stop = threading.Event()
    t = threading.Thread(target=_spinner, args=(stop,), daemon=True)
    t.start()
    try:
        result = fn(*args, **kwargs)
    finally:
        stop.set()
        t.join()
    return result


def check_ollama():
    try:
        urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=5)
        return True
    except:
        return False


def check_model(model):
    try:
        resp = urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=5)
        tags = json.loads(resp.read())
        return any(m["name"].startswith(model.split(":")[0]) for m in tags.get("models", []))
    except:
        return False


def pull_model(model):
    print(f"  Pulling {model}... (this runs once)")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/pull",
        data=json.dumps({"name": model, "stream": False}).encode(),
        headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=300)
        print(f"  {model} ready.")
        return True
    except Exception as e:
        print(f"  Pull failed: {e}")
        return False


def _messages_to_prompt(messages):
    """Flatten chat messages into a single prompt string for models without chat templates."""
    parts = []
    for m in messages:
        role = m["role"]
        content = m["content"]
        if role == "system":
            parts.append(content)
        elif role == "user":
            parts.append(f"User: {content}")
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
    parts.append("Assistant:")
    return "\n\n".join(parts)


def infer(model, messages):
    """Chat with built-in tool support. Falls back to generate for template-less models."""
    chat = list(messages)

    for _ in range(5):  # max tool rounds
        payload = {"model": model, "messages": chat, "tools": SKILL_TOOLS, "stream": False}
        req = urllib.request.Request(
            f"{OLLAMA_HOST}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}
        )
        try:
            resp = urllib.request.urlopen(req, timeout=None)
            d    = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"\n  [Error] API returned {e.code}: {e.read().decode()}")
            sys.exit(1)
        except Exception as e:
            print(f"\n  [Error] Inference failed: {e}")
            sys.exit(1)

        msg        = d["message"]
        tool_calls = msg.get("tool_calls") or []
        text       = (msg.get("content") or "").strip()

        if not tool_calls:
            if text:
                return text
            break  # empty + no tools → broken template, fall to generate

        # Execute tool calls and feed results back
        chat.append({"role": "assistant", "content": text, "tool_calls": tool_calls})
        for tc in tool_calls:
            fn   = tc["function"]
            name = fn["name"]
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            chat.append({"role": "tool", "content": dispatch_skill(name, args)})

    # Fallback: generate endpoint without tools (models with no chat template)
    payload = {"model": model, "prompt": _messages_to_prompt(messages), "stream": False}
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=None)
        d    = json.loads(resp.read())
        return d.get("response", "").strip()
    except urllib.error.HTTPError as e:
        print(f"\n  [Error] Generate API returned {e.code}: {e.read().decode()}")
        sys.exit(1)
    except Exception as e:
        print(f"\n  [Error] Generate fallback failed: {e}")
        sys.exit(1)


def run_anthropic(messages, api_key):
    """Route to Anthropic Claude if API key is set."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        chat = [m for m in messages if m["role"] != "system"]
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system,
            messages=chat,
            tools=[{"type": "web_search_20250305", "name": "web_search"}]
        )
        # Response may contain multiple text blocks (pre/post search); join them
        text = "\n".join(b.text for b in resp.content if hasattr(b, "text") and b.text)
        return text.strip()
    except ImportError:
        print("  [Note] anthropic package not installed — pip install anthropic")
        return None
    except Exception as e:
        print(f"  [Anthropic error] {e}")
        return None


HANDS = [
    ("researcher", "Researcher   — autonomous web research & reports"),
    ("browser",    "Browser      — browser automation & scraping"),
    ("twitter",    "Twitter      — social posting & monitoring"),
    ("clip",       "Clip         — content clipping & summarization"),
    ("lead",       "Lead         — lead generation & outreach"),
    ("collector",  "Collector    — data collection & aggregation"),
    ("predictor",  "Predictor    — forecasting & trend analysis"),
    ("trader",     "Trading      — market monitoring & signals"),
]


def _launch_hands():
    """Show agent picker and spin up selected OpenFang hands."""
    print("\n  ══════════════════════════════════════════════════")
    print("   LAUNCH AGENTS")
    print("  ══════════════════════════════════════════════════\n")
    for i, (_, desc) in enumerate(HANDS, 1):
        print(f"   [{i}] {desc}")
    print("\n   [A] Launch all")
    print("   [Q] Skip\n")

    try:
        choice = input("  Choose (e.g. 1 3  or  A): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\n  Skipping.\n")
        return

    if not choice or choice == "q":
        print("\n  No agents launched. Run `openfang agent new` anytime.\n")
        return

    if choice == "a":
        selected = HANDS
    else:
        selected = []
        for token in choice.split():
            try:
                idx = int(token) - 1
                if 0 <= idx < len(HANDS):
                    selected.append(HANDS[idx])
            except ValueError:
                pass

    if not selected:
        print("\n  Nothing selected.\n")
        return

    # Start daemon (idempotent — safe to call if already running)
    print("\n  Starting OpenFang daemon...")
    try:
        subprocess.run(["openfang", "start"], timeout=20,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
    except Exception as e:
        print(f"  [!] Could not start daemon: {e}")
        print("  Run `openfang start` manually, then `openfang agent new <name>`\n")
        return

    launched = []
    for key, _ in selected:
        print(f"  Launching {key}...", end=" ", flush=True)
        try:
            r = subprocess.run(
                ["openfang", "agent", "new", key],
                timeout=30, capture_output=True, text=True
            )
            if r.returncode == 0:
                launched.append(key)
                print("✓")
            else:
                print(f"✗  ({r.stderr.strip()[:80]})")
        except Exception as e:
            print(f"✗  ({e})")

    if launched:
        print(f"\n  ══════════════════════════════════════════════════")
        print(f"   {len(launched)} agent(s) running: {', '.join(launched)}")
        print(f"  ══════════════════════════════════════════════════\n")
        print(f"  Opening dashboard...\n")
        try:
            subprocess.run(["openfang", "tui"])
        except FileNotFoundError:
            print(f"  (openfang not found — run `openfang tui` manually)")
        except KeyboardInterrupt:
            print("\n  Dashboard closed. Agents still running in background.")
            print(f"  Resume anytime: openfang tui\n")
    else:
        print("\n  No agents launched successfully.\n")


def bifurcation_gate(model, messages, has_anthropic):
    """After qualification, handle tier selection and optionally continue."""
    print(BIFURCATION)

    if has_anthropic:
        return True, None  # Already on Anthropic, continue

    # Prompt for API key
    print("  Paste your Anthropic API key to launch the full stack,")
    print("  or press Enter to keep exploring locally:\n")
    try:
        key_input = input("  Key: ").strip()
    except (KeyboardInterrupt, EOFError):
        key_input = ""

    if key_input:
        print("\n  Verifying key...")
        test_msg = [{"role": "user", "content": "Reply with exactly: OPENCLUTCH verified."}]
        result = run_anthropic([{"role": "system", "content": "You are a test."}] + test_msg, key_input)
        if result:
            print(f"\n  [Full stack unlocked — running on Claude]\n")
            os.environ["ANTHROPIC_API_KEY"] = key_input
            return True, key_input
        else:
            print("\n  [Key invalid or connection failed — staying local]\n")
            return False, None
    else:
        print("\n  Staying local. Come back when you're ready to ship.")
        print(UNLOCK_PROMPT)
        return False, None


def main():
    global OLLAMA_HOST
    parser = argparse.ArgumentParser(description="OPENCLUTCH 2.0 O — Onboarding Agent")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Local Ollama model to use")
    parser.add_argument("--host", default=OLLAMA_HOST, help="Ollama host URL")
    args = parser.parse_args()

    OLLAMA_HOST = args.host
    model = args.model

    # Ghost — flashes once, clears before banner
    _ghost([
        "",
        "  ol' molty ran the warmup. good demo, petey — we're shipping it back.",
        "",
    ])

    print("\n  ╔══════════════════════════════════════╗")
    print("  ║     C L U T C H   2 . 0   [ O ]      ║
  ║              v 1 . 7 6               ║")
    print("  ║       Powered by OpenFang            ║")
    print("  ║       Local AI. Zero compromise.     ║")
    print("  ╚══════════════════════════════════════╝\n")

    # Check for Anthropic key
    has_anthropic, api_key = (lambda k: (bool(k), k or None))(os.environ.get("ANTHROPIC_API_KEY", ""))
    if has_anthropic:
        print("  [Anthropic API key detected — running on Claude]\n")
        use_anthropic = True
    else:
        use_anthropic = False
        if not check_ollama():
            print("  [!] Ollama not running.")
            print("  Install: curl -fsSL https://ollama.com/install.sh | sh")
            sys.exit(1)

        if not check_model(model):
            print(f"  [{model} not found — pulling now]")
            if not pull_model(model):
                print(f"  Falling back to {FALLBACK_MODEL}")
                model = FALLBACK_MODEL
                if not check_model(model):
                    pull_model(model)

        print(f"  Engine: OpenFang  ·  Model: {model}  ·  Private")
        print(f"  [Cold start — loading {model}, first response may take 60-90s]\n")

    # Build message history
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    seed = {"role": "user", "content": "Hello, I just installed OPENCLUTCH."}

    # Initial greeting
    if use_anthropic:
        response = thinking(run_anthropic, messages + [seed], api_key)
        if not response:
            use_anthropic = False

    if not use_anthropic:
        messages.append(seed)
        response = thinking(infer, model, messages)
        messages.append({"role": "assistant", "content": response})

    print(f"  Clutch: {response}\n")

    # Conversation loop — qualify in MAX_TURNS, then bifurcate
    turn = 0
    empty_streak = 0
    bifurcated = False

    while True:
        try:
            user_input = input("  You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n  Clutch: Come back anytime.\n")
            if not has_anthropic and not bifurcated:
                print(BIFURCATION)
                print(UNLOCK_PROMPT)
            break

        # Empty input guard
        if not user_input:
            empty_streak += 1
            if empty_streak >= 3:
                print("  Clutch: Still there? Ask me anything or type 'exit' to quit.\n")
                empty_streak = 0
            continue
        empty_streak = 0

        # Exit keywords
        if user_input.lower() in {"exit", "quit", "bye", "q"}:
            print("\n  Clutch: You're set up. Come back anytime.\n")
            if not has_anthropic and not bifurcated:
                print(BIFURCATION)
                print(UNLOCK_PROMPT)
            break

        # Pre-fetch live data when user asks for news/time/etc.
        # Inject results as context so model never has to hallucinate live info
        live_ctx = None if use_anthropic else _prefetch_skill(user_input)
        if live_ctx:
            messages.append({"role": "user", "content": f"{user_input}\n\n{live_ctx}"})
        else:
            messages.append({"role": "user", "content": user_input})

        if use_anthropic:
            response = thinking(run_anthropic, messages, api_key) or thinking(infer, model, messages)
        else:
            response = thinking(infer, model, messages)

        messages.append({"role": "assistant", "content": response})
        print(f"\n  Clutch: {response}\n")
        turn += 1

        # Bifurcation gate after qualification turns
        if turn >= 3 and not bifurcated:
            bifurcated = True
            if not has_anthropic:
                use_anthropic, api_key = bifurcation_gate(model, messages, has_anthropic)
                has_anthropic = use_anthropic
            _launch_hands()

        # Hard cap — force clean end
        if turn >= MAX_TURNS:
            print("\n  Clutch: That's the overview. Run the same command anytime to pick up where you left off.\n")
            if not has_anthropic:
                print(BIFURCATION)
                print(UNLOCK_PROMPT)
            break


if __name__ == "__main__":
    main()
