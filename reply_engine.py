#!/usr/bin/env python3
"""
OPENCLUTCH Reply Engine
Finds relevant posts, generates value-add replies with catchphrase closers,
posts from @OPENCLUTCH2. @theradiomachine referral mode also supported.

Usage:
    python3 reply_engine.py                    # run one cycle from @OPENCLUTCH2
    python3 reply_engine.py --account radio    # run one cycle from @theradiomachine
    python3 reply_engine.py --dry-run          # generate replies without posting

Cron (3 reply cycles/day):
    0 9,14,20 * * * python3 /path/to/reply_engine.py >> /tmp/reply_engine.log 2>&1
"""

import argparse, json, os, random, re, subprocess, sys, time
import urllib.request, urllib.parse

# ── Config ────────────────────────────────────────────────────────────────────

OLLAMA_HOST   = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
REPLY_MODEL   = os.environ.get("REPLY_MODEL", "qwen3:8b")
STATE_FILE    = os.path.expanduser("~/.openclutch_reply_state.json")

# Search keywords — any match triggers candidate evaluation
SEARCH_QUERIES = [
    "ollama self-hosted",
    "local LLM privacy",
    "running AI locally",
    "open source agents",
    "self-hosted AI stack",
    "cloud AI costs",
    "llama local inference",
    "small models hardware",
    "data sovereignty AI",
    "agent automation local",
]

# Minimum engagement to bother replying to
MIN_LIKES    = 5
MIN_REPLIES  = 0
MAX_REPLIES_PER_RUN = 3
SEARCH_RESULTS_PER_QUERY = 10

CATCHPHRASES = [
    "Running AI locally isn't a compromise. It's a position.",
    "Most people are renting intelligence. You can own it.",
    "The agent doesn't need to phone home. That's the point.",
    "Centralized AI is a subscription to someone else's priorities. Opt out.",
    "An agent that works for you in the clutch — not one you work for.",
]

RADIO_REFERRAL_SUFFIX = "\n\n→ @OPENCLUTCH2"


# ── Credential loader ─────────────────────────────────────────────────────────

def _vault_get(key):
    try:
        r = subprocess.run(
            ["openfang", "vault", "get", key],
            capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip().splitlines()[-1].strip() if r.returncode == 0 else None
    except Exception:
        return None


def _get_creds(account="openclutch"):
    """Load Twitter OAuth credentials for the given account."""
    if account == "radio":
        return {
            "api_key":       _vault_get("TWITTER_RADIOMACHINE_API_KEY"),
            "api_secret":    _vault_get("TWITTER_RADIOMACHINE_API_SECRET"),
            "access_token":  _vault_get("TWITTER_RADIOMACHINE_ACCESS_TOKEN"),
            "access_secret": _vault_get("TWITTER_RADIOMACHINE_ACCESS_SECRET"),
            "bearer":        urllib.parse.unquote(_vault_get("TWITTER_RADIOMACHINE_BEARER") or ""),
        }
    else:
        return {
            "api_key":       _vault_get("TWITTER_OPENCLUTCH2_API_KEY"),
            "api_secret":    _vault_get("TWITTER_OPENCLUTCH2_API_SECRET"),
            "access_token":  _vault_get("TWITTER_OPENCLUTCH2_ACCESS_TOKEN"),
            "access_secret": _vault_get("TWITTER_OPENCLUTCH2_ACCESS_SECRET"),
            "bearer":        urllib.parse.unquote(_vault_get("TWITTER_OPENCLUTCH2_BEARER") or ""),
        }


# ── State management ──────────────────────────────────────────────────────────

def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"replied_ids": [], "catchphrase_index": 0}


def _save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _next_catchphrase(state):
    idx = state.get("catchphrase_index", 0) % len(CATCHPHRASES)
    state["catchphrase_index"] = (idx + 1) % len(CATCHPHRASES)
    return CATCHPHRASES[idx]


# ── Ollama inference ──────────────────────────────────────────────────────────

def _generate_reply(post_text, catchphrase, account="openclutch"):
    if account == "radio":
        persona = (
            "You are @theradiomachine, a builder and longtime presence in the AI/tech space. "
            "You're referring your audience to a new project called OPENCLUTCH. "
            "Add one genuine insight. 1-2 sentences. "
            f"End with exactly: {catchphrase}{RADIO_REFERRAL_SUFFIX}"
        )
    else:
        persona = (
            "You are @OPENCLUTCH2 — a local-first AI agent platform for builders. "
            "Someone posted about AI, local inference, or agents. "
            "Add one genuinely useful insight. 1-2 sentences max. No fluff, no hype. "
            f"End the reply with exactly this line: {catchphrase}"
        )

    messages = [
        {"role": "system", "content": persona},
        {"role": "user",   "content": f'Post to reply to:\n"""\n{post_text}\n"""\n\nWrite the reply:'},
    ]

    payload = {"model": REPLY_MODEL, "messages": messages, "stream": False}
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        d    = json.loads(resp.read())
        return (d["message"]["content"] or "").strip()
    except Exception as e:
        return None


# ── Twitter client ────────────────────────────────────────────────────────────

def _build_client(creds):
    try:
        import tweepy
        client = tweepy.Client(
            bearer_token=creds["bearer"],
            consumer_key=creds["api_key"],
            consumer_secret=creds["api_secret"],
            access_token=creds["access_token"],
            access_token_secret=creds["access_secret"],
            wait_on_rate_limit=True,
        )
        return client
    except ImportError:
        print("[!] tweepy not installed — pip install tweepy")
        sys.exit(1)


def _search_posts(client, query, max_results=10):
    """Search recent tweets matching query. Returns list of {id, text, likes}."""
    try:
        import tweepy
        resp = client.search_recent_tweets(
            query=f"{query} -is:retweet lang:en",
            max_results=max_results,
            tweet_fields=["public_metrics", "author_id", "text"],
        )
        if not resp.data:
            return []
        results = []
        for t in resp.data:
            metrics = t.public_metrics or {}
            results.append({
                "id":     str(t.id),
                "text":   t.text,
                "likes":  metrics.get("like_count", 0),
                "replies": metrics.get("reply_count", 0),
                "author": str(t.author_id),
            })
        return results
    except Exception as e:
        print(f"  [search error] {e}")
        return []


def _post_reply(client, reply_text, tweet_id, dry_run=False):
    if dry_run:
        print(f"\n  [DRY RUN] Would reply to {tweet_id}:\n  {reply_text}\n")
        return True
    try:
        client.create_tweet(text=reply_text, in_reply_to_tweet_id=tweet_id)
        return True
    except Exception as e:
        print(f"  [post error] {e}")
        return False


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(account="openclutch", dry_run=False):
    print(f"[reply_engine] account=@{'theradiomachine' if account=='radio' else 'OPENCLUTCH2'} dry_run={dry_run}")

    state   = _load_state()
    replied = set(state.get("replied_ids", []))
    creds   = _get_creds(account)

    # Validate credentials loaded
    if not creds["api_key"] or not creds["bearer"]:
        print(f"[!] Credentials not found for account={account}.")
        print("    Run: openfang vault set TWITTER_<ACCOUNT>_API_KEY")
        sys.exit(1)

    client  = _build_client(creds)

    candidates = []
    query = random.choice(SEARCH_QUERIES)
    print(f"  Searching: {query}")
    posts = _search_posts(client, query, max_results=SEARCH_RESULTS_PER_QUERY)

    for post in posts:
        if post["id"] in replied:
            continue
        if post["likes"] < MIN_LIKES:
            continue
        # Skip very long posts (probably threads, harder to add value)
        if len(post["text"]) > 500:
            continue
        candidates.append(post)

    if not candidates:
        print("  No suitable candidates found this cycle.")
        return

    # Sort by likes desc, take top candidates
    candidates.sort(key=lambda x: x["likes"], reverse=True)
    targets = candidates[:MAX_REPLIES_PER_RUN]

    posted = 0
    for post in targets:
        catchphrase = _next_catchphrase(state)
        reply = _generate_reply(post["text"], catchphrase, account)
        if not reply:
            print(f"  [!] Failed to generate reply for {post['id']}")
            continue

        # Trim to 280 chars if needed
        if len(reply) > 280:
            # Keep catchphrase, trim the body
            lines = reply.split("\n")
            body  = " ".join(lines[:-1]).strip()
            closer = lines[-1].strip()
            body  = body[:280 - len(closer) - 2]
            reply = f"{body}\n{closer}"

        print(f"\n  → Replying to tweet {post['id']} ({post['likes']} likes)")
        print(f"  Post: {post['text'][:100]}...")
        print(f"  Reply: {reply[:150]}...")

        ok = _post_reply(client, reply, post["id"], dry_run=dry_run)
        if ok:
            replied.add(post["id"])
            posted += 1
            if not dry_run:
                time.sleep(5)   # space replies

    state["replied_ids"] = list(replied)[-500:]   # keep last 500
    _save_state(state)
    print(f"\n  Done. {posted} replies posted.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OPENCLUTCH Reply Engine")
    parser.add_argument("--account",  default="openclutch", choices=["openclutch", "radio"],
                        help="Which account to post from")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Generate replies without posting")
    parser.add_argument("--query",    default=None,
                        help="Override search query (default: random from list)")
    args = parser.parse_args()

    if args.query:
        SEARCH_QUERIES.insert(0, args.query)

    run(account=args.account, dry_run=args.dry_run)
