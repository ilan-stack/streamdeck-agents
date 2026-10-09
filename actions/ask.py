#!/usr/bin/env python3
"""ask.py "<instruction>" < text  ->  Claude Haiku's answer on stdout (empty on failure).

Calls the Anthropic API directly (fast; the claude CLI takes ~9 s to start). Key, first found:
$ANTHROPIC_API_KEY or ~/.config/streamdeck/anthropic_key.
Billed per use - Haiku, a fraction of a cent per call.
"""
import json
import os
import sys
import urllib.error
import urllib.request

MODEL = "claude-haiku-5-5"


def api_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    cfg = os.path.expanduser("~/.config/streamdeck/anthropic_key")
    if os.path.exists(cfg):
        return open(cfg).read().strip()
    print("ask.py: no API key (put one in ~/.config/streamdeck/anthropic_key)", file=sys.stderr)
    raise SystemExit(1)


def main():
    instruction = sys.argv[1]
    text = sys.stdin.read() if not sys.stdin.isatty() else ""
    body = {"model": MODEL, "max_tokens": 1024, "system": instruction,
            "messages": [{"role": "user", "content": text or "(empty)"}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", json.dumps(body).encode(), {
        "x-api-key": api_key(), "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        res = json.load(urllib.request.urlopen(req, timeout=40))
        print("".join(b.get("text", "") for b in res.get("content", [])).strip())
    except urllib.error.HTTPError as e:
        print(f"ask.py: HTTP {e.code} {e.read()[:200]!r}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
