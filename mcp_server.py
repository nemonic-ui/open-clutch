#!/usr/bin/env python3
"""
OPENCLUTCH — MCP Skills Server
Exposes built-in skills as MCP tools for Claude Desktop, Claude Code,
or any MCP-compatible client.

Install:
    pip install mcp

Run (stdio, for MCP clients):
    python3 mcp_server.py

Claude Desktop config (~/.config/claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "openclutch": {
          "command": "python3",
          "args": ["/path/to/open-clutch/mcp_server.py"]
        }
      }
    }

Claude Code (.claude/settings.json or via /mcp add):
    {
      "mcpServers": {
        "openclutch": {
          "command": "python3",
          "args": ["/path/to/open-clutch/mcp_server.py"]
        }
      }
    }
"""

import re, urllib.request, urllib.parse
from datetime import datetime
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("openclutch-skills")


# ── Skills ──────────────────────────────────────────────────────────────────

@mcp.tool()
def web_search(query: str) -> str:
    """Search the web for current news, information, or any topic.
    Returns the top 3 results with title, URL, and snippet."""
    data = urllib.parse.urlencode({"q": query, "kl": "us-en"}).encode()
    req  = urllib.request.Request(
        "https://html.duckduckgo.com/html/",
        data=data,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
    )
    try:
        html     = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="ignore")
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


@mcp.tool()
def get_datetime() -> str:
    """Get the current local date and time."""
    return datetime.now().strftime("%A, %B %d, %Y — %I:%M %p")


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
