#!/usr/bin/env python3
"""
OPENCLUTCH — Reply Bot
Finds relevant posts, scores them internally, replies with positioned perspectives.

Usage:
    python3 clutch_reply_bot.py              # dry run — scores only, no posting
    python3 clutch_reply_bot.py --post       # live mode — posts replies
    python3 clutch_reply_bot.py --test       # score a single tweet interactively
"""

import argparse, json, os, sys, time, random, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone, timedelta

# ── Credentials ────────────────────────────────────────────────────────────────
# Autonomous_Robot app (OPENCLUTCH2 project) — used for search (bearer) + posting
API_KEY    = os.environ.get("TWITTER_API_KEY",    "urWhlVml1uNSEkSiRUCWZmzsH")
API_SECRET = os.environ.get("TWITTER_API_SECRET", "jS8Il1GkLpShDi9Y80ztsruNPBWxKxVITzkeykdJ6WKs81iEKG")
BEARER     = os.environ.get("TWITTER_BEARER_TOKEN",
    "AAAAAAAAAAAAAAAAAAAAAJXq7gEAAAAAh9UInFMjk5OGElr0G%2FnI6HCwdhk%3Dqff1BrRoAq55n8CPuSa4nBtsjeqf0APMmNJ4gKXOpFA6mmeJan")

# @theradiomachine — trusted account (10yr old, 387 followers) for posting
ACC_TOKEN  = os.environ.get("TWITTER_ACCESS_TOKEN",        "759975138896912384-VqHEtClUgcwngcLOOE5EEmETMiVrycN")
ACC_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "uH5eZYH3JGnGTQ9Z3gQtv6dYfCSHZCTpvCoCGBbCnLLOf")

# ── Config ─────────────────────────────────────────────────────────────────────
MIN_SCORE        = 5          # Minimum relevance to engage
COOLDOWN_HOURS   = 48         # Don't reply to same user within this window
MAX_REPLIES      = 3          # Max replies per run
MEMORY_FILE      = os.path.expanduser("~/.openclutch_reply_memory.json")
QUEUE_FILE       = os.path.expanduser("~/tweet_queue.txt")

# ── Search queries ─────────────────────────────────────────────────────────────
SEARCH_QUERIES = [
    "local LLM running inference lang:en -is:retweet",
    "ollama llama agent lang:en -is:retweet",
    "autonomous AI agent open source lang:en -is:retweet",
    "local AI privacy compute lang:en -is:retweet",
    "building AI agent stack lang:en -is:retweet",
    "LLM on my machine lang:en -is:retweet",
]

# ── Scoring keywords ───────────────────────────────────────────────────────────
HIGH_SIGNAL   = ["local", "ollama", "autonomous", "agent", "inference", "open source",
                 "self-hosted", "privacy", "compute", "llm", "on-device", "run locally",
                 "gguf", "quantized", "mistral", "llama", "hermes", "openai alternative"]
MEDIUM_SIGNAL = ["ai", "model", "automation", "workflow", "tool", "build", "ship",
                 "deploy", "api", "token", "context", "prompt", "chatbot", "assistant"]
INVITES_REPLY = ["?", "thoughts", "what do you", "anyone", "who else", "unpopular opinion",
                 "hot take", "agree", "disagree", "should i", "worth it"]
DISQUALIFY    = ["crypto", "nft", "giveaway", "follow back", "dm me", "buy now",
                 "sponsored", "ad ", "#ad", "click here"]


# ── Memory ─────────────────────────────────────────────────────────────────────

def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE) as f:
            return json.load(f)
    return {"replied_to": {}, "replied_tweets": []}


def save_memory(mem):
    with open(MEMORY_FILE, "w") as f:
        json.dump(mem, f, indent=2)


def already_replied(mem, author_id):
    entry = mem["replied_to"].get(author_id)
    if not entry:
        return False
    last = datetime.fromisoformat(entry["last_reply"])
    return datetime.now(timezone.utc) - last < timedelta(hours=COOLDOWN_HOURS)


def record_reply(mem, author_id, username, tweet_id, reply_text):
    mem["replied_to"][author_id] = {
        "username": username,
        "last_reply": datetime.now(timezone.utc).isoformat(),
        "tweet_id": tweet_id,
    }
    mem["replied_tweets"].append({
        "tweet_id": tweet_id,
        "reply": reply_text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ── Positioned perspectives queue ──────────────────────────────────────────────

def load_perspectives():
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE) as f:
            lines = [l.strip() for l in f if l.strip()]
        if lines:
            return lines
    # Fallback built-in perspectives
    return [
        "Running AI locally isn't a compromise. It's a position.",
        "The agent doesn't need to phone home. That's the point.",
        "Every token you send to the cloud is data you gave away.",
        "You don't need a DevOps team to run an agent stack. You need one command.",
        "The next wave isn't bigger models. It's smaller models that run on your hardware.",
        "Centralized AI is a subscription to someone else's priorities.",
        "Your laptop has more compute than it's ever been asked to use.",
        "Open source AI isn't catching up. It's already here.",
    ]


# ── Intent signals ─────────────────────────────────────────────────────────────

# High intent — actively looking, frustrated, asking for help
INTENT_HIGH = [
    "anyone recommend", "looking for", "trying to get", "can't get",
    "struggling with", "how do i", "how do you", "best way to",
    "what's a good", "any suggestions", "help me", "stuck on",
    "frustrated", "can't figure out", "switching from", "tired of paying",
    "api costs", "too expensive", "rate limit", "what should i use",
    "where do i start", "just started", "new to this",
]

# Medium intent — curious, engaged observer, not committed
INTENT_MEDIUM = [
    "interesting", "following this", "watching this space", "curious about",
    "thinking about", "considering", "might try", "worth exploring",
    "haven't tried", "want to try", "sounds good", "cool project",
    "this is neat", "didn't know", "just learned", "first time seeing",
]

# Low intent — committed elsewhere or just broadcasting
INTENT_LOW = [
    "we use", "at my company", "our team uses", "we've been using",
    "works great for us", "production", "already running",
]


def intent_score(tweet_text):
    """
    Intent gate — where is this person in their journey?
    Returns: 'high', 'medium', 'low', 'skip'

    high   = actively looking, frustrated, asking — open door
    medium = curious observer, conceptually engaged — soft open door
    low    = committed elsewhere — skip
    skip   = lurker / no opinion signal
    """
    text = tweet_text.lower()

    if any(sig in text for sig in INTENT_LOW):
        return "low"

    if any(sig in text for sig in INTENT_HIGH):
        return "high"

    if any(sig in text for sig in INTENT_MEDIUM):
        return "medium"

    # Has a question mark but no other signal — treat as medium
    if "?" in text:
        return "medium"

    return "skip"


# ── Relevance scoring ──────────────────────────────────────────────────────────

def relevance_score(tweet_text):
    """
    Relevance gate. 1-10.
    10 = talking about exactly what we care about, reply is clearly valid.
    1  = off topic, reply would feel like spam.
    5  = good enough to engage.
    """
    text = tweet_text.lower()
    s = 0

    # Hard disqualifiers
    for term in DISQUALIFY:
        if term in text:
            return 0

    # High signal topics — core relevance
    matches = sum(1 for kw in HIGH_SIGNAL if kw in text)
    s += min(matches * 2, 6)

    # Medium signal — adjacent topics
    med_matches = sum(1 for kw in MEDIUM_SIGNAL if kw in text)
    s += min(med_matches, 2)

    # Invites engagement
    if any(trigger in text for trigger in INVITES_REPLY):
        s += 2

    return min(s, 10)


def should_engage(tweet_text):
    """
    Combined gate: relevance + intent.
    Returns (engage: bool, relevance: int, intent: str)

    Rules:
    - Relevance >= 5 AND intent high or medium → engage
    - High intent alone (relevance >= 3) → engage — they're asking for help
    - Low intent → skip regardless of relevance
    - Intent skip → need relevance >= 7 to overcome silence
    """
    rel = relevance_score(tweet_text)
    intent = intent_score(tweet_text)

    if rel == 0:
        return False, rel, intent

    if intent == "low":
        return False, rel, intent

    if intent == "high" and rel >= 3:
        return True, rel, intent

    if intent == "medium" and rel >= 5:
        return True, rel, intent

    if intent == "skip" and rel >= 7:
        return True, rel, intent

    return False, rel, intent


# ── Reply construction ─────────────────────────────────────────────────────────

def build_reply(tweet_text, perspectives, intent):
    """
    Reply varies by intent:
    - high intent  → lead with a question that opens the door
    - medium intent → positioned perspective with a soft hook
    - skip/high rel → straight positioned perspective
    """
    perspective = random.choice(perspectives)

    if intent == "high":
        # They're looking — ask the question that starts the conversation
        openers = [
            f"What's stopping you from running it locally? Usually it's one dependency. {perspective}",
            f"What stack are you on right now? {perspective}",
            f"What's the blocker — setup or hardware? {perspective}",
            f"Have you looked at running it on your own machine? {perspective}",
        ]
        reply = random.choice(openers)
    else:
        # Curious or high relevance — plant the perspective
        connectors = [
            f"Exactly this. {perspective}",
            f"This is the conversation worth having. {perspective}",
            f"Worth saying louder. {perspective}",
            f"Right direction. {perspective}",
            f"The framing matters here. {perspective}",
        ]
        reply = random.choice(connectors)

    # Trim if over limit
    if len(reply) > 275:
        reply = perspective

    return reply


# ── Twitter API ────────────────────────────────────────────────────────────────

import hmac, hashlib, base64, string


def pe(s):
    return urllib.parse.quote(str(s), safe='')


def oauth_header(method, url, extra_params=None):
    import random, string
    nonce = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
    ts = str(int(time.time()))
    params = {
        'oauth_consumer_key': API_KEY,
        'oauth_nonce': nonce,
        'oauth_signature_method': 'HMAC-SHA1',
        'oauth_timestamp': ts,
        'oauth_token': ACC_TOKEN,
        'oauth_version': '1.0',
    }
    if extra_params:
        params.update(extra_params)
    param_str = '&'.join(pe(k) + '=' + pe(v) for k, v in sorted(params.items()))
    base_str = method + '&' + pe(url) + '&' + pe(param_str)
    signing_key = pe(API_SECRET) + '&' + pe(ACC_SECRET)
    sig = base64.b64encode(
        hmac.new(signing_key.encode(), base_str.encode(), hashlib.sha1).digest()
    ).decode()
    # Auth header only includes oauth_ params
    oauth_only = {k: v for k, v in params.items() if k.startswith('oauth_')}
    oauth_only['oauth_signature'] = sig
    parts = [pe(k) + '="' + pe(v) + '"' for k, v in sorted(oauth_only.items())]
    return 'OAuth ' + ', '.join(parts)


def search_tweets(query, max_results=10):
    """Search recent tweets via Twitter API v2."""
    encoded = urllib.parse.urlencode({
        "query": query,
        "max_results": max_results,
        "tweet.fields": "author_id,created_at,text",
        "expansions": "author_id",
        "user.fields": "username",
    })
    url = f"https://api.twitter.com/2/tweets/search/recent?{encoded}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {BEARER}"})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except Exception as e:
        print(f"  [Search error] {e}")
        return None


def post_quote(tweet_id, username, reply_text):
    """Standalone mention tweet — @mentions user, links their post, plants perspective.
    Free tier blocks reply/quote unless mentioned first; plain tweets with @mention work."""
    url = "https://api.twitter.com/2/tweets"
    tweet_url = f"https://x.com/{username}/status/{tweet_id}"
    # Format: @username <perspective> — @OPENCLUTCH2 <url>
    suffix = f" — @OPENCLUTCH2 {tweet_url}"
    max_body = 280 - len(f"@{username} ") - len(suffix)
    if len(reply_text) > max_body:
        reply_text = reply_text[:max_body]
    text = f"@{username} {reply_text}{suffix}"
    body = json.dumps({"text": text}).encode()
    auth = oauth_header("POST", url)
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": auth,
        "Content-Type": "application/json",
    }, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        return data["data"]["id"]
    except urllib.error.HTTPError as e:
        print(f"  [Post error] {e.code}: {e.read().decode()}")
        return None


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OPENCLUTCH Reply Bot")
    parser.add_argument("--post",  action="store_true", help="Live mode — actually post replies")
    parser.add_argument("--test",  action="store_true", help="Score a tweet from stdin")
    parser.add_argument("--limit", type=int, default=MAX_REPLIES, help="Max replies per run")
    args = parser.parse_args()

    perspectives = load_perspectives()
    mem = load_memory()

    # ── Test mode: score a single tweet ──
    if args.test:
        print("\n  Paste a tweet to score (Ctrl+C to quit):\n")
        while True:
            try:
                text = input("  Tweet: ").strip()
                if text:
                    engage, rel, intent = should_engage(text)
                    reply = build_reply(text, perspectives, intent) if engage else None
                    print(f"\n  Relevance: {rel}/10  Intent: {intent}  Engage: {'yes' if engage else 'no'}")
                    if reply:
                        print(f"  Reply: {reply}\n")
                    else:
                        print(f"  Decision: skip\n")
            except (KeyboardInterrupt, EOFError):
                break
        return

    # ── Normal run ──
    mode = "LIVE" if args.post else "DRY RUN"
    print(f"\n  OPENCLUTCH Reply Bot — {mode}")
    print(f"  Min score: {MIN_SCORE}/10  ·  Cooldown: {COOLDOWN_HOURS}h  ·  Max replies: {args.limit}\n")

    replied = 0
    seen_tweet_ids = set(mem.get("replied_tweets", []))

    for query in SEARCH_QUERIES:
        if replied >= args.limit:
            break

        print(f"  Searching: {query[:60]}...")
        results = search_tweets(query)
        if not results or "data" not in results:
            continue

        # Build author_id → username map from includes
        users = {u["id"]: u["username"]
                 for u in results.get("includes", {}).get("users", [])}

        for tweet in results["data"]:
            if replied >= args.limit:
                break

            tweet_id   = tweet["id"]
            author_id  = tweet["author_id"]
            username   = users.get(author_id, "unknown")
            text       = tweet["text"]

            # Skip if already replied to this tweet
            if tweet_id in mem.get("replied_tweets", []):
                continue

            # Cooldown check
            if already_replied(mem, author_id):
                print(f"  [{username}] skip — cooldown active")
                continue

            # Combined gate
            engage, rel, intent = should_engage(text)
            print(f"  [{username}] rel={rel}/10 intent={intent} — {text[:70]}...")

            if not engage:
                print(f"           skip\n")
                continue

            # Build reply — voice changes based on intent
            reply_text = build_reply(text, perspectives, intent)
            print(f"           reply: {reply_text}")

            if args.post:
                posted_id = post_quote(tweet_id, username, reply_text)
                if posted_id:
                    record_reply(mem, author_id, username, tweet_id, reply_text)
                    save_memory(mem)
                    replied += 1
                    print(f"           quoted [{posted_id}]\n")
                    time.sleep(random.uniform(8, 20))  # Human-like spacing
            else:
                replied += 1
                print(f"           (dry run — not posted)\n")

    print(f"  Done. {replied} replies {'posted' if args.post else 'found'}.\n")


if __name__ == "__main__":
    main()
