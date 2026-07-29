"""
person2meta - KeenTools Cloud API test
Step 1: open a session and see exactly what the API hands back, before we
build the rest of the pipeline around assumed field names.

SETUP:
  Set your API key as an environment variable first, don't paste it into
  this file or into chat. On Windows (Command Prompt):
      set KEENTOOLS_API_KEY=your_key_here
  Then run this script in that same terminal window:
      python cloud_api_test.py
"""

import os
import json
import requests  # pip install requests, if you don't have it yet

API_KEY = os.environ.get("KEENTOOLS_API_KEY")

if not API_KEY:
    print("[person2meta] KEENTOOLS_API_KEY environment variable not set. "
          "Set it in this terminal window before running the script.")
    raise SystemExit(1)

# --- CONFIG: point this at a small folder of test images ---
IMAGE_COUNT = 2  # just testing with 2 for now to conserve trial heads
# -------------------------------------------------------------

response = requests.post(
    "https://api.cloud.keentools.io/v1/avatar/init",
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    },
    json={"image_count": IMAGE_COUNT},
)

print(f"[person2meta] Status code: {response.status_code}")
print("[person2meta] Raw response body:")
try:
    print(json.dumps(response.json(), indent=2))
except ValueError:
    print(response.text)
