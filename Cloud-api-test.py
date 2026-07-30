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
    "https://api.keentools.workers.dev/v1/avatar/init",
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    },
    json={"image_count": IMAGE_COUNT},
)

print("[person2meta] Status code:", response.status_code)
data = response.json()
print("[person2meta] Raw response body:")
print(json.dumps(data, indent=2))

# --- Step 2: upload the actual image files to the returned URLs ---
# EDIT these two paths to point at 2 real test images on your machine.
LOCAL_IMAGE_PATHS = [
    r"C:\path\to\your\first_test_image.png",
    r"C:\path\to\your\second_test_image.png",
]

avatar_id = data["avatar_id"]
img_urls = data["img_urls"]

if len(LOCAL_IMAGE_PATHS) != len(img_urls):
    print(f"[person2meta] Mismatch: {len(LOCAL_IMAGE_PATHS)} local paths but "
          f"{len(img_urls)} upload URLs. Fix LOCAL_IMAGE_PATHS above.")
    raise SystemExit(1)

for local_path, url in zip(LOCAL_IMAGE_PATHS, img_urls):
    with open(local_path, "rb") as f:
        image_bytes = f.read()
    put_response = requests.put(url, data=image_bytes)
    print(f"[person2meta] Uploaded {local_path} -> status {put_response.status_code}")

print(f"[person2meta] Done. avatar_id = {avatar_id}")
print("[person2meta] STOPPING HERE ON PURPOSE — not calling /process yet. "
      "Check your credit balance on the KeenTools dashboard before we proceed "
      "to confirm upload alone didn't spend anything.")
