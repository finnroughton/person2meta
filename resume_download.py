"""
person2meta - resume downloading an already-completed avatar
Use this when process() already succeeded but the download step didn't
finish, so you don't waste a credit re-running the whole pipeline.

SETUP:
  $env:KEENTOOLS_API_KEY = "your_key_here"
  python resume_download.py
"""

import os
import time
import tkinter as tk
from tkinter import filedialog
import requests

API_KEY = os.environ.get("KEENTOOLS_API_KEY")
BASE_URL = "https://api.keentools.workers.dev"

if not API_KEY:
    print("[person2meta] KEENTOOLS_API_KEY not set.")
    raise SystemExit(1)

AVATAR_ID = "019fb3dd-9549-7371-a230-6466cb4c6a0a"


def pick_output_path() -> str:
    root = tk.Tk()
    root.withdraw()
    path = filedialog.asksaveasfilename(
        title="Save reconstructed head as...",
        defaultextension=".zip",
        filetypes=[("ZIP archive", "*.zip")],
        initialfile="natalie_head.zip",
    )
    root.destroy()
    return path


def main():
    output_zip_path = pick_output_path()
    if not output_zip_path:
        print("[person2meta] No output location chosen. Aborting.")
        raise SystemExit(1)

    params = {"mesh_format": "obj", "texture": "jpg"}
    url = f"{BASE_URL}/v1/avatar/{AVATAR_ID}/get-3d-model"

    while True:
        resp = requests.get(url, headers={"Authorization": f"Bearer {API_KEY}"}, params=params)
        resp.raise_for_status()
        result = resp.json()
        event = result.get("event")

        if event == "retry-after":
            wait_sec = result["data"]["time_sec"]
            print(f"[person2meta] Not quite ready, waiting {wait_sec}s (not billed)...")
            time.sleep(wait_sec)
            continue
        elif event == "redirect":
            download_url = result["data"]["url"]
            print("[person2meta] Ready (billed once). Downloading...")
            model_resp = requests.get(download_url)
            model_resp.raise_for_status()
            with open(output_zip_path, "wb") as f:
                f.write(model_resp.content)
            print(f"[person2meta] Saved to {output_zip_path}")
            return
        else:
            print(f"[person2meta] Unexpected response: {result}")
            return


if __name__ == "__main__":
    main()
