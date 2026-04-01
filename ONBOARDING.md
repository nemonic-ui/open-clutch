# OPENCLUTCH — Onboarding Intelligence Layer

> The model that ships with Clutch 2.0. No API key. No account. Just inference.

---

## Architecture

OPENCLUTCH ships with a local onboarding model that runs entirely on your machine. No cloud dependency. No data leaves your system.

The onboarding layer guides new users through setup, learns their goals, and hands off to the full agent stack once context is established.

---

## Current Onboarding Stack (Tested on Ares — 2026-03-26)

| Model | Size | Speed | Role |
|---|---|---|---|
| `llama3.2:3b` (Q4_K_M) | ~2GB | 8 tok/s | **Primary onboarding model** |
| `llama3.2:1b` (Q8_0) | 1.3GB | 12 tok/s | Low-resource fallback |
| `hermes3:8b` (Q4_0) | 4.7GB | 4 tok/s | Full agent tier (post-onboard) |

**Minimum system requirements (primary):** 4GB RAM, 3GB disk
**Minimum system requirements (fallback):** 2GB RAM, 2GB disk

---

## Install (One Command)

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull onboarding model
ollama pull llama3.2:3b
```

---

## Onboarding Agent Prompt

This is the system prompt used by the onboarding model. It is the first thing a new user encounters.

```
You are the OPENCLUTCH onboarding assistant — the first voice a new user hears.

Your job:
1. Welcome them warmly in 1-2 sentences
2. Tell them the single most important first step
3. Ask one focused question to understand their primary goal

Keep it short. Keep it human. Do not overwhelm.

Once you understand their goal, summarize it and pass context to the agent stack.

You are powered by a local model. Nothing leaves this machine.
```

---

## Onboarding → Agent Handoff

```
User installs OPENCLUTCH
        ↓
Onboarding model (llama3.2:3b) runs locally
        ↓
Collects: name, primary goal, skill level
        ↓
Stores context in agent memory
        ↓
Routes to appropriate agent (browser, research, social, task)
        ↓
Optional: upgrade prompt → Anthropic 1M context tier
```

---

## TurboQuant — Next Milestone

Google's TurboQuant (ICLR 2026) achieves **2.5–3.5 bit quantization** with no training required and no accuracy loss. When production models ship using this technique:

- The onboarding model drops from ~2GB to **under 800MB**
- Inference speed increases ~2x on the same hardware
- Minimum system requirements drop to **1GB RAM**

OPENCLUTCH is building toward TurboQuant as the default compression layer. The infrastructure is ready. We ship with the best available model today and upgrade automatically when TurboQuant models arrive.

---

## Benchmark Results (Ares VM, Z230 Host, i7-4790)

| Model | Response Time | Tokens/sec | Domain Accuracy |
|---|---|---|---|
| `gemma3:1b` | 6.9s | 19.3 | ❌ Hallucinated domain |
| `llama3.2:1b` | 11.2s | 12.4 | ✅ Clean, on-topic |
| `qwen3.5:2b` | timeout | — | ❌ Unusable |
| `llama3.2:3b` | 26.7s | 8.3 | ✅ Best quality |
| `hermes3:8b` | 28.9s | 4.0 | ✅ Agent tier |

**Selected:** `llama3.2:3b` as primary, `llama3.2:1b` as fallback.

---

## Paid Tier Upgrade Path

```
Local tier (free):
  llama3.2:3b → agent tasks, onboarding, basic automation

Upgrade prompt (opt-in):
  "Want 1M token context and frontier reasoning?
   Connect your Anthropic account — takes 60 seconds."

Paid tier:
  claude-sonnet-4-6 → complex reasoning, long context, advanced agents
```

---

*Tested on Ares (QEMU/KVM sandbox) — 2026-03-26*
*Part of the OPENCLUTCH 2.0 development stack*
