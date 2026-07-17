---
name: social_search
description: "Search and fetch FULL content (bodies + comment threads, not just titles) from Hacker News, Reddit, and X — the working access recipes for each platform's blocks and quirks"
roles_allowed: [worker]
triggers: [reddit, hacker news, twitter, "x.com", social media, user complaints, community sentiment, forum posts, what people say]
---

## Overview

Use this skill when research needs real user voices from social platforms.
Each platform blocks naive access differently; these are the recipes proven
working on this box (2026-07-11). Prefer full post bodies and comment threads
over titles — title-only evidence is materially weaker (flag it if it's all
you can get).

## Hacker News — easiest, use freely

Algolia API, no auth, generous limits. Full comment text included.

```bash
# search comments (or tags=story for posts)
curl -s "https://hn.algolia.com/api/v1/search?query=YOUR+QUERY&tags=comment&hitsPerPage=20"
# full thread by story id
curl -s "https://hn.algolia.com/api/v1/items/43200146"
```

## Reddit — JSON is blocked; RSS is the door

Every JSON endpoint (old./www. `.json`, search.json, Pushshift, redlib,
Jina proxy) returns 403 to non-logged-in clients. **RSS/Atom endpoints work**
with a desktop browser User-Agent, and the per-post feed carries the FULL
post body plus comments as entries:

```bash
UA="Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
# 1. discover posts (titles + links)
curl -s -A "$UA" "https://old.reddit.com/r/ClaudeAI/search.rss?q=YOUR+QUERY&restrict_sr=1&sort=relevance"
# 2. fetch FULL content for a post found in step 1 (body + comment thread)
curl -s -A "$UA" "https://old.reddit.com/r/SUBREDDIT/comments/POST_ID/.rss"
```

Parse the Atom `<entry>` elements: first entry = the post (author + body
HTML in `<content>`), remaining entries = comments. Double-unescape the
HTML entities, strip tags. Be polite: ~1 request/2s, don't hammer.

## X / Twitter — authenticated CLI, two-step warm-up

The `twitter` CLI (twitter-cli ≥0.8.5, installed) does search + tweet/thread
fetch, but needs (a) cookies bridged from the cache file and (b) a
ClientTransaction cache re-seed (X's home page changed format; the CLI's own
init 404s everything without this):

```bash
# warm-up (once per hour of X work; safe to re-run)
bash scripts/x-ct-reseed.sh
export TWITTER_AUTH_TOKEN=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.cache/twitter-cli/cookies.json')))['auth_token'])")
export TWITTER_CT0=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.cache/twitter-cli/cookies.json')))['ct0'])")

# then:
twitter -c search "your query" --max 20   # -c = compact JSON, LLM-friendly
twitter -c tweet <tweet_url_or_id>         # tweet + its replies (the thread)
```

**Follow-up-post pattern (verified 2026-07-17):** when a post says
"Repo👇" / "link below" / "🧵", the payload link lives in the AUTHOR'S OWN
first reply, not the root post. `twitter -c tweet` returns the replies —
scan them for same-author entries before declaring a link unobtainable
(runs 1dac0e17 + 75a88777 both burned steps hunting a GitHub repo whose
link was sitting in the author's follow-up reply). Note: the CLI command
is `tweet`, not `thread` — 0.8.5 renamed it. `src/web_fetch.py`'s
`fetch_x_tweet` is reply-aware since 2026-07-17 (direct-CLI rung 0,
BACKLOG #26): it returns the thread with author follow-ups in their own
section and t.co links pre-resolved, falling back to the old reply-blind
rungs only when the CLI/cookies are absent.

NEVER echo, log, or write the cookie values anywhere — export only, as
above. If cookies have expired (auth errors), run
`~/.openclaw/workspace/scripts/x-twitter-cli-refresh.sh` to pull fresh ones
from the saved browser profile, then re-export.

Known tweets can also be fetched via `src/web_fetch.py` (`fetch_x_tweet` —
direct CLI thread w/ replies → Jina Reader → wrapper CLI → oEmbed fallbacks).

## Quality gates

- Quote users verbatim; keep author + date + link for every quote.
- Mark evidence depth honestly: `full-content` vs `title-only`.
- X search "Top" ranking is engagement-biased — expect noise; filter for
  first-person concrete accounts, not viral commentary.
- Respect the platform: read-only access, no posting, modest request rates.
