"""
person2meta - test the get-status endpoint
Not marked [BILLED] in the docs, so this should be safe to call freely
while we confirm what it actually returns.

SETUP:
  $env:KEENTOOLS_API_KEY = "your_key_here"
  python check_status.py
"""

import os
import json
import requests

API_KEY = os.environ.get("KEENTOOLS_API_KEY")
BASE_URL = "https://api.keentools.workers.dev"

if not API_KEY:
    print("[person2meta] KEENTOOLS_API_KEY not set.")
    raise SystemExit(1)

# Paste in an avatar_id from a previous init call (even one you haven't
# processed yet -- we just want to see what get-status says about it).
AVATAR_ID = "019fb3ba-68e2-7691-98c5-4c20e3474114"

resp = requests.get(
    f"{BASE_URL}/v1/avatar/{AVATAR_ID}/get-status",
    headers={"Authorization": f"Bearer {API_KEY}"},
)

print(f"[person2meta] Status code: {resp.status_code}")
try:
    print(json.dumps(resp.json(), indent=2))
except ValueError:
    print(resp.text)
