#!/usr/bin/env bash
# Smoke-test a running pcd-sidecar: one request of each question type.
# Usage: ./smoke.sh http://192.168.0.50:8765
set -euo pipefail

URL="${1:?usage: smoke.sh <base-url>}"

echo "== GET /healthz =="
curl -sf "$URL/healthz" | python3 -m json.tool
echo

echo "== POST /v1/systemone (noul) =="
curl -sf -X POST "$URL/v1/systemone" -H 'Content-Type: application/json' -d '{
  "model": "smoke",
  "state": {"goal": "write is_palindrome(s)", "result": "def is_palindrome(s): return s == s[::-1]"},
  "questions": {
    "matches_goal": {
      "type": "noul",
      "instructions": "does the result address the stated goal?",
      "criteria": {"true": "it does", "false": "it does not"}
    }
  }
}' | python3 -m json.tool
echo

echo "== POST /v1/systemone (choice) =="
curl -sf -X POST "$URL/v1/systemone" -H 'Content-Type: application/json' -d '{
  "model": "smoke",
  "state": {"goal": "run the auth test suite", "result": "27 passed in 1.84s; exit code 0"},
  "questions": {
    "outcome": {
      "type": "choice",
      "instructions": "did the result satisfy the goal?",
      "criteria": {"done": "fully satisfied", "blocked": "cannot proceed", "unclear": "ambiguous"}
    }
  }
}' | python3 -m json.tool
echo

echo "== POST /v1/systemone (score) =="
curl -sf -X POST "$URL/v1/systemone" -H 'Content-Type: application/json' -d '{
  "model": "smoke",
  "state": {"goal": "write a report", "result": "A brief, mostly-complete report with one missing section."},
  "questions": {
    "quality": {
      "type": "score",
      "instructions": "rate the quality of the result",
      "criteria": ["poor", "adequate", "excellent"]
    }
  }
}' | python3 -m json.tool
echo

echo "== POST /v1/systemone (malformed -> expect 400) =="
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST "$URL/v1/systemone" -H 'Content-Type: application/json' -d '{not json'

echo "smoke test complete"
