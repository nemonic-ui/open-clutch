#!/usr/bin/env python3
"""
OPENCLUTCH Overnight CPU Marathon
Tests: cold/warm start latency, tokens/sec, tool calling, context coherence
Machines: Z230 (host), Prometheus (.111), Ares (.83)
Logs to: /tmp/marathon_results.jsonl
"""

import json, time, subprocess, urllib.request, datetime, os, sys

LOG_FILE = "/tmp/marathon_results.jsonl"
Z230_OLLAMA  = "http://192.168.122.1:11434"
PROM_OLLAMA  = "http://localhost:11434"
PROM_OPENFANG = "http://192.168.122.111:4200"

TIMESTAMP = lambda: datetime.datetime.utcnow().isoformat()

def log(record):
    record["ts"] = TIMESTAMP()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")
    print(f"  [{record['ts'][:19]}] {record.get('test','?')} | {record.get('model','?')} | {record.get('summary','')}")

def ollama_chat(host, model, messages, timeout=180):
    payload = {"model": model, "messages": messages, "stream": False}
    req = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        d = json.loads(resp.read())
        elapsed = time.time() - t0
        return {
            "ok": True,
            "content": d["message"]["content"],
            "elapsed": round(elapsed, 2),
            "total_duration": d.get("total_duration", 0) / 1e9,
            "eval_count": d.get("eval_count", 0),
            "tps": round(d.get("eval_count", 0) / max(d.get("eval_duration", 1) / 1e9, 0.001), 1),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "elapsed": round(time.time() - t0, 2)}

def ollama_chat_with_tools(host, model, messages, tools, timeout=180):
    payload = {"model": model, "messages": messages, "tools": tools, "stream": False}
    req = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        d = json.loads(resp.read())
        elapsed = time.time() - t0
        msg = d.get("message", {})
        tool_calls = msg.get("tool_calls", [])
        return {
            "ok": True,
            "content": msg.get("content", ""),
            "tool_calls": tool_calls,
            "tool_called": len(tool_calls) > 0,
            "elapsed": round(elapsed, 2),
            "tps": round(d.get("eval_count", 0) / max(d.get("eval_duration", 1) / 1e9, 0.001), 1),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "elapsed": round(time.time() - t0, 2)}

# ── Test 1: Cold/Warm start latency ───────────────────────────────────────────
def test_latency(host, model, label):
    print(f"\n  [LATENCY] {label} / {model}")
    prompt = [{"role": "user", "content": "Reply with exactly: READY"}]

    # Cold (first call)
    r = ollama_chat(host, model, prompt, timeout=300)
    log({"test": "latency_cold", "machine": label, "model": model,
         "elapsed": r.get("elapsed"), "tps": r.get("tps"), "ok": r["ok"],
         "summary": f"cold={r.get('elapsed')}s tps={r.get('tps')}"})

    time.sleep(2)

    # Warm (second call)
    r2 = ollama_chat(host, model, prompt, timeout=120)
    log({"test": "latency_warm", "machine": label, "model": model,
         "elapsed": r2.get("elapsed"), "tps": r2.get("tps"), "ok": r2["ok"],
         "summary": f"warm={r2.get('elapsed')}s tps={r2.get('tps')}"})

# ── Test 2: Tool calling ───────────────────────────────────────────────────────
SEARCH_TOOL = [{
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current information",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"}
            },
            "required": ["query"]
        }
    }
}]

WEATHER_TOOL = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string"},
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
            },
            "required": ["location"]
        }
    }
}]

def test_tool_calling(host, model, label):
    print(f"\n  [TOOLS] {label} / {model}")
    cases = [
        ("web_search", SEARCH_TOOL, "What is the latest news about local AI inference?"),
        ("get_weather", WEATHER_TOOL, "What's the weather in Austin Texas right now?"),
        ("multi_intent", SEARCH_TOOL + WEATHER_TOOL, "Search for AI news and get the weather in San Francisco"),
    ]
    for case_name, tools, prompt in cases:
        r = ollama_chat_with_tools(host, model, [{"role": "user", "content": prompt}], tools)
        called = r.get("tool_called", False)
        tool_names = [tc.get("function", {}).get("name") for tc in r.get("tool_calls", [])]
        log({"test": f"tool_{case_name}", "machine": label, "model": model,
             "tool_called": called, "tools_invoked": tool_names,
             "elapsed": r.get("elapsed"), "ok": r["ok"],
             "summary": f"called={called} tools={tool_names}"})
        time.sleep(3)

# ── Test 3: Context coherence (long conversation) ────────────────────────────
COHERENCE_TURNS = [
    "My name is Alex and I'm building a local AI agent stack.",
    "I'm using Ollama on a Linux machine with 32GB RAM.",
    "My primary use case is automated research and web browsing.",
    "I prefer Python over JavaScript for tooling.",
    "What's the first thing you'd recommend I build?",
    "What was my name again?",
    "What operating system am I using?",
    "What's my preferred programming language?",
    "Summarize everything you know about my setup in 3 bullet points.",
    "Based on my use case, which of my preferences is most relevant?",
]

def test_coherence(host, model, label):
    print(f"\n  [COHERENCE] {label} / {model}")
    messages = []
    failures = 0
    checks = {
        5: ["alex", "Alex"],
        6: ["linux", "Linux", "32"],
        7: ["python", "Python"],
        8: ["alex", "Alex", "linux", "Linux", "ollama", "Ollama"],
    }
    for i, turn in enumerate(COHERENCE_TURNS):
        messages.append({"role": "user", "content": turn})
        r = ollama_chat(host, model, messages, timeout=120)
        if not r["ok"]:
            log({"test": "coherence_fail", "machine": label, "model": model,
                 "turn": i, "error": r.get("error"),
                 "summary": f"turn {i} failed: {r.get('error')}"})
            failures += 1
            messages.append({"role": "assistant", "content": "[no response]"})
            continue

        content = r["content"]
        messages.append({"role": "assistant", "content": content})

        if i in checks:
            expected = checks[i]
            found = any(kw.lower() in content.lower() for kw in expected)
            log({"test": f"coherence_turn_{i}", "machine": label, "model": model,
                 "turn": i, "coherent": found, "tps": r.get("tps"),
                 "response_snippet": content[:120],
                 "summary": f"turn {i} coherent={found} tps={r.get('tps')}"})
        time.sleep(2)

    log({"test": "coherence_summary", "machine": label, "model": model,
         "total_turns": len(COHERENCE_TURNS), "failures": failures,
         "summary": f"completed {len(COHERENCE_TURNS)} turns, {failures} failures"})

# ── Test 4: Onboarding simulation (3-turn qual arc) ──────────────────────────
SYSTEM_ONBOARD = """You are CLUTCH 2.0. You qualify users in exactly 3 turns.
Turn 1: Ask what they want to automate.
Turn 2: Reflect their goal, ask what to tackle first: browser, research, posting, or scheduling.
Turn 3: Confirm match. End with: 'Ready to launch the stack?'
Keep responses under 80 words."""

ONBOARD_TURNS = [
    "Hey I just installed this. What can you do?",
    "I want to automate my job search and research.",
    "Let's start with research.",
]

def test_onboarding(host, model, label):
    print(f"\n  [ONBOARD] {label} / {model}")
    messages = [{"role": "system", "content": SYSTEM_ONBOARD}]
    for i, turn in enumerate(ONBOARD_TURNS):
        messages.append({"role": "user", "content": turn})
        r = ollama_chat(host, model, messages, timeout=120)
        if not r["ok"]:
            log({"test": f"onboard_turn_{i}", "machine": label, "model": model,
                 "turn": i, "ok": False, "summary": f"failed: {r.get('error')}"})
            continue
        content = r["content"]
        messages.append({"role": "assistant", "content": content})
        on_script = i == 2 and "ready to launch" in content.lower()
        log({"test": f"onboard_turn_{i}", "machine": label, "model": model,
             "turn": i, "tps": r.get("tps"), "elapsed": r.get("elapsed"),
             "on_script": on_script if i == 2 else None,
             "response_snippet": content[:120],
             "summary": f"turn {i} tps={r.get('tps')} on_script={on_script if i==2 else 'N/A'}"})
        time.sleep(2)

# ── Test 5: Sustained load (10 parallel-ish requests) ────────────────────────
def test_sustained(host, model, label, rounds=10):
    print(f"\n  [SUSTAINED] {label} / {model} x{rounds}")
    prompts = [
        "Name 3 open source AI frameworks.",
        "What is a vector database?",
        "Explain tokenization in one sentence.",
        "What makes a good system prompt?",
        "What is RAG?",
        "Name a use case for a browser automation agent.",
        "What is the difference between llama3.2:3b and 8b?",
        "What is context window size?",
        "Explain KV cache in simple terms.",
        "What is chain-of-thought prompting?",
    ]
    times = []
    tps_list = []
    for i in range(rounds):
        prompt = prompts[i % len(prompts)]
        r = ollama_chat(host, model, [{"role": "user", "content": prompt}], timeout=90)
        if r["ok"]:
            times.append(r["elapsed"])
            tps_list.append(r.get("tps", 0))
        time.sleep(1)

    avg_t = round(sum(times) / len(times), 2) if times else 0
    avg_tps = round(sum(tps_list) / len(tps_list), 1) if tps_list else 0
    log({"test": "sustained_load", "machine": label, "model": model,
         "rounds": rounds, "avg_elapsed": avg_t, "avg_tps": avg_tps,
         "min_elapsed": min(times) if times else 0,
         "max_elapsed": max(times) if times else 0,
         "summary": f"avg={avg_t}s avg_tps={avg_tps} min={min(times) if times else 0}s max={max(times) if times else 0}s"})

# ── Main ──────────────────────────────────────────────────────────────────────
def run_suite(host, models, label):
    print(f"\n{'='*60}")
    print(f"  SUITE: {label}")
    print(f"{'='*60}")
    log({"test": "suite_start", "machine": label, "model": "", "summary": f"starting suite on {label}"})

    for model in models:
        test_latency(host, model, label)
        test_tool_calling(host, model, label)
        test_onboarding(host, model, label)
        test_coherence(host, model, label)
        test_sustained(host, model, label, rounds=10)
        time.sleep(5)

    log({"test": "suite_done", "machine": label, "model": "", "summary": f"suite complete on {label}"})


if __name__ == "__main__":
    print(f"\n  OPENCLUTCH OVERNIGHT MARATHON")
    print(f"  Start: {TIMESTAMP()}")
    print(f"  Log: {LOG_FILE}\n")

    log({"test": "marathon_start", "model": "", "summary": "overnight marathon started"})

    # Z230 — heavy models
    run_suite(Z230_OLLAMA, ["hermes3-16k:latest", "qwen3:8b", "llama3.2:3b"], "Z230")

    # Prometheus — local llama3.2
    run_suite(PROM_OLLAMA, ["llama3.2:3b", "qwen2.5:7b"], "Prometheus")

    log({"test": "marathon_done", "model": "", "summary": "overnight marathon complete"})
    print(f"\n  DONE: {TIMESTAMP()}")
    print(f"  Results: {LOG_FILE}")
