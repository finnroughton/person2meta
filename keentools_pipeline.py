"""
person2meta - KeenTools Cloud API full pipeline test
Runs the complete sequence: init -> upload -> process -> poll -> download.

COST WARNING: calling process() spends a credit hold, captured on success.
Polling get-3d-model is free UNTIL it returns "redirect" -- that response
IS billed, and calling again after that (even by accident) bills again.
This script stops polling the instant it sees "redirect" and never calls
get-3d-model again afterward for the same avatar_id.

SETUP:
  Set your API key as an environment variable first.
  PowerShell:  $env:KEENTOOLS_API_KEY = "your_key_here"
  Then run:    python keentools_pipeline.py
"""

import os
import json
import time
import tkinter as tk
from tkinter import filedialog
import requests  # pip install requests

API_KEY = os.environ.get("KEENTOOLS_API_KEY")
BASE_URL = "https://api.keentools.workers.dev"

if not API_KEY:
    print("[person2meta] KEENTOOLS_API_KEY environment variable not set.")
    raise SystemExit(1)

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

# ---- No more hardcoded paths -- picked interactively when the script runs ----
def pick_images() -> list[str]:
    """Opens a file-picker window, returns selected image paths."""
    root = tk.Tk()
    root.withdraw()

    filetypes = [
        ("Image files", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"),
        ("All files", "*.*"),
    ]
    paths = filedialog.askopenfilenames(
        title="Select 2-15 face images",
        filetypes=filetypes,
    )
    root.destroy()
    return list(paths)


def pick_output_path() -> str:
    """Opens a save-file window, returns the chosen output .zip path."""
    root = tk.Tk()
    root.withdraw()

    path = filedialog.asksaveasfilename(
        title="Save reconstructed head as...",
        defaultextension=".zip",
        filetypes=[("ZIP archive", "*.zip")],
        initialfile="reconstructed_head.zip",
    )
    root.destroy()
    return path
# ------------------------------------------------------------------------


def init_avatar(image_count: int) -> dict:
    resp = requests.post(
        f"{BASE_URL}/v1/avatar/init",
        headers=HEADERS,
        json={"image_count": image_count},
    )
    resp.raise_for_status()
    return resp.json()


def upload_images(img_urls: list[str], local_paths: list[str]) -> None:
    for local_path, url in zip(local_paths, img_urls):
        with open(local_path, "rb") as f:
            image_bytes = f.read()
        put_resp = requests.put(url, data=image_bytes)
        put_resp.raise_for_status()
        print(f"[person2meta] Uploaded {local_path} -> status {put_resp.status_code}")


def start_processing(avatar_id: str, num_images: int) -> None:
    # Placeholder focal length values -- ~26mm is a typical smartphone
    # equivalent. Replace with real EXIF-derived values later if accuracy
    # matters; for this shell, hardcoding is fine.
    focal_length_values = [26.0] * num_images

    body = {
        "expressions_enabled": False,
        "focal_length_type": {
            "focal_length_type": "manual",
            "focal_length_values": focal_length_values,
        },
    }

    resp = requests.post(
        f"{BASE_URL}/v1/avatar/{avatar_id}/process",
        headers=HEADERS,
        json=body,
    )
    if resp.status_code == 402:
        print("[person2meta] STOPPED: insufficient credits.")
        raise SystemExit(1)
    resp.raise_for_status()
    print(f"[person2meta] Reconstruction started (status {resp.status_code}).")


def wait_until_completed(avatar_id: str, poll_interval_sec: int = 5) -> None:
    """Polls the free get-status endpoint until reconstruction finishes."""
    url = f"{BASE_URL}/v1/avatar/{avatar_id}/get-status"
    while True:
        resp = requests.get(url, headers={"Authorization": f"Bearer {API_KEY}"})
        resp.raise_for_status()
        status = resp.json().get("status")
        print(f"[person2meta] status: {status}")

        if status == "completed":
            return
        elif status == "failed":
            print("[person2meta] STOPPED: reconstruction failed.")
            raise SystemExit(1)
        else:
            time.sleep(poll_interval_sec)


def download_model(avatar_id: str, output_zip_path: str) -> None:
    """Calls get-3d-model exactly once, after status is already confirmed
    completed. This call is billed, so it only happens a single time."""
    params = {"mesh_format": "obj", "texture": "jpg"}
    resp = requests.get(
        f"{BASE_URL}/v1/avatar/{avatar_id}/get-3d-model",
        headers={"Authorization": f"Bearer {API_KEY}"},
        params=params,
    )
    resp.raise_for_status()
    result = resp.json()

    if result.get("event") == "redirect":
        download_url = result["data"]["url"]
        model_resp = requests.get(download_url)
        model_resp.raise_for_status()
        with open(output_zip_path, "wb") as f:
            f.write(model_resp.content)
        print(f"[person2meta] Saved to {output_zip_path}")
    else:
        # Shouldn't happen if status was already "completed", but just in
        # case, don't call again automatically -- surface it instead.
        print(f"[person2meta] Unexpected response, not retrying automatically: {result}")


def main():
    print("[person2meta] Opening image picker...")
    local_image_paths = pick_images()

    if not (2 <= len(local_image_paths) <= 15):
        print(f"[person2meta] STOPPED: selected {len(local_image_paths)} image(s), "
              f"but the API requires between 2 and 15.")
        raise SystemExit(1)

    print("[person2meta] Opening save-location picker...")
    output_zip_path = pick_output_path()
    if not output_zip_path:
        print("[person2meta] No output location chosen. Aborting.")
        raise SystemExit(1)

    num_images = len(local_image_paths)

    print("[person2meta] Step 1: init")
    init_data = init_avatar(num_images)
    avatar_id = init_data["avatar_id"]
    img_urls = init_data["img_urls"]
    print(f"[person2meta] avatar_id = {avatar_id}")

    print("[person2meta] Step 2: upload")
    upload_images(img_urls, local_image_paths)

    print("[person2meta] Step 3: process (this holds credits)")
    start_processing(avatar_id, num_images)

    print("[person2meta] Step 4: wait for completion (free polling)")
    wait_until_completed(avatar_id)

    print("[person2meta] Step 5: download model (this single call is billed)")
    download_model(avatar_id, output_zip_path)


if __name__ == "__main__":
    main()
