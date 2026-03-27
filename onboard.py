#!/usr/bin/env python3
"""
OPENCLUTCH — Onboarding Agent
Clutch 2.0 O | Powered by OpenFang | Tested on Ares (llama3.2:3b via Ollama)

O is for Open. OpenFang is the engine. Nothing leaves this machine.

Usage:
    python3 openclutch_onboard.py
    python3 openclutch_onboard.py --model llama3.2:1b   # low-resource fallback
    python3 openclutch_onboard.py --model hermes3:8b     # full agent tier
"""

import argparse, json, sys, time, urllib.request, urllib.error, os

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = "llama3.2:3b"
FALLBACK_MODEL = "llama3.2:1b"

SYSTEM_PROMPT = """You are the OPENCLUTCH onboarding assistant — the first voice a new user hears.

Your job:
1. Welcome them warmly in 1-2 sentences
2. Tell them the single most important first step
3. Ask one focused question to understand their primary goal

Keep it short. Keep it human. Do not overwhelm.

Once you understand their goal, summarize it and confirm what agent will help them most.

You are powered by a local model. Nothing leaves this machine."""

UPGRADE_PROMPT = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Level up with 1M token context + frontier reasoning
  Recommended provider: Anthropic Claude
  No affiliation required. Simply the best.

  Set your key:  export ANTHROPIC_API_KEY=your_key
  Then restart:  python3 openclutch_onboard.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def _ghost(lines, duration=1.2):
    """Print lines briefly, then erase them. One-time ghost — doesn't linger."""
    for line in lines:
        print(line)
    sys.stdout.flush()
    time.sleep(duration)
    sys.stdout.write('\033[F\033[2K' * len(lines))
    sys.stdout.flush()


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


def infer(model, messages):
    """Send messages to Ollama chat endpoint, return response text."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": False
    }
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        d = json.loads(resp.read())
        return d["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        print(f"\n[Error] API returned {e.code}: {e.read().decode()}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[Error] Inference failed: {e}")
        sys.exit(1)


def check_anthropic_upgrade():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        return True, api_key
    return False, None


def run_anthropic(messages, api_key):
    """Optional: route to Anthropic Claude if API key is set."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        chat = [m for m in messages if m["role"] != "system"]
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system,
            messages=chat
        )
        return resp.content[0].text.strip()
    except ImportError:
        print("[Note] anthropic package not installed — pip install anthropic")
        return None
    except Exception as e:
        print(f"[Anthropic error] {e}")
        return None


def main():
    global OLLAMA_HOST
    parser = argparse.ArgumentParser(description="OPENCLUTCH 2.0 O — Onboarding Agent")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Local Ollama model to use")
    parser.add_argument("--host", default=OLLAMA_HOST, help="Ollama host URL")
    args = parser.parse_args()

    OLLAMA_HOST = args.host
    model = args.model

    # Ghost — flashes once, never seen again
    _ghost([
        "",
        "  ol' molty ran the warmup. good demo, petey — we're shipping it back.",
        "",
    ])

    print("\n  ╔══════════════════════════════════════╗")
    print("  ║       C L U T C H   2 . 0   [ O ]   ║")
    print("  ║       Powered by OpenFang            ║")
    print("  ║       Local AI. Zero compromise.     ║")
    print("  ╚══════════════════════════════════════╝\n")

    # Check for Anthropic upgrade
    has_anthropic, api_key = check_anthropic_upgrade()
    if has_anthropic:
        print("  [Anthropic API key detected — running on Claude]\n")
        use_anthropic = True
    else:
        use_anthropic = False
        # Check Ollama
        if not check_ollama():
            print("  [!] Ollama not running. Install: curl -fsSL https://ollama.com/install.sh | sh")
            sys.exit(1)

        if not check_model(model):
            print(f"  [{model} not found locally]")
            if not pull_model(model):
                print(f"  Falling back to {FALLBACK_MODEL}")
                model = FALLBACK_MODEL
                if not check_model(model):
                    pull_model(model)

        print(f"  Engine: OpenFang  ·  Model: {model}  ·  Private\n")

    # Conversation loop
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Initial greeting
    if use_anthropic:
        response = run_anthropic(messages + [{"role": "user", "content": "Hello, I just installed OPENCLUTCH."}], api_key)
        if not response:
            use_anthropic = False

    if not use_anthropic:
        messages.append({"role": "user", "content": "Hello, I just installed OPENCLUTCH."})
        response = infer(model, messages)
        messages.append({"role": "assistant", "content": response})

    print(f"  Clutch: {response}\n")

    # Interactive loop
    turn = 0
    while True:
        try:
            user_input = input("  You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n  Session ended.")
            if not has_anthropic:
                print(UPGRADE_PROMPT)
            break

        if not user_input:
            continue

        if user_input.lower() in ["exit", "quit", "bye"]:
            print("\n  Clutch: You're set up. Come back anytime.")
            if not has_anthropic:
                print(UPGRADE_PROMPT)
            break

        messages.append({"role": "user", "content": user_input})

        if use_anthropic:
            response = run_anthropic(messages, api_key) or infer(model, messages)
        else:
            response = infer(model, messages)

        messages.append({"role": "assistant", "content": response})
        print(f"\n  Clutch: {response}\n")

        turn += 1
        # After 3 turns, gently surface upgrade if local
        if turn == 3 and not has_anthropic:
            print("  ─────────────────────────────────────")
            print("  [Optional] Set ANTHROPIC_API_KEY to upgrade to 1M context + Claude.")
            print("  ─────────────────────────────────────\n")


if __name__ == "__main__":
    main()
