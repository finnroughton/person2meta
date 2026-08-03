"""
person2meta - full orchestrator, reusable for any head
Picks images -> KeenTools reconstruction -> Blender FBX conversion ->
writes a config file that conform_to_metahuman.py (run inside Unreal) reads.

No code editing needed between runs -- just run this script again for a
new head.

SETUP:
  $env:KEENTOOLS_API_KEY = "your_key_here"
  python run_pipeline.py
"""

import os
import json
import time
import subprocess
import zipfile
import tkinter as tk
from tkinter import filedialog
import requests

API_KEY = os.environ.get("KEENTOOLS_API_KEY")
BASE_URL = "https://api.keentools.workers.dev"

# ---- EDIT ONCE: paths that don't change between runs ----
BLENDER_EXE = r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
CONVERT_SCRIPT = os.path.join(os.path.dirname(__file__), "convert_obj_to_fbx.py")
CONFIG_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "person2meta_config.json")
WORK_DIR = os.path.join(os.path.dirname(__file__), "person2meta_work")
# -----------------------------------------------------------

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def pick_images() -> list[str]:
    root = tk.Tk()
    root.withdraw()
    paths = filedialog.askopenfilenames(
        title="Select 2-15 face images for reconstruction",
        filetypes=[("Image files", "*.jpg *.jpeg *.png"), ("All files", "*.*")],
    )
    root.destroy()
    return list(paths)


def pick_portrait() -> str:
    root = tk.Tk()
    root.withdraw()
    path = filedialog.askopenfilename(
        title="Select ONE front-facing portrait for MetaHuman conform",
        filetypes=[("Image files", "*.jpg *.jpeg *.png"), ("All files", "*.*")],
    )
    root.destroy()
    return path


def ask_head_name() -> str:
    root = tk.Tk()
    root.withdraw()
    from tkinter import simpledialog
    name = simpledialog.askstring(
        "Head name", "Short name for this head (used for asset/file names):"
    )
    root.destroy()
    return name or f"head_{int(time.time())}"


def init_avatar(image_count: int) -> dict:
    resp = requests.post(f"{BASE_URL}/v1/avatar/init", headers=HEADERS,
                          json={"image_count": image_count})
    resp.raise_for_status()
    return resp.json()


def upload_images(img_urls: list[str], local_paths: list[str]) -> None:
    for local_path, url in zip(local_paths, img_urls):
        with open(local_path, "rb") as f:
            put_resp = requests.put(url, data=f.read())
        put_resp.raise_for_status()
        print(f"[person2meta] Uploaded {os.path.basename(local_path)}")


def start_processing(avatar_id: str, num_images: int) -> None:
    body = {
        "expressions_enabled": False,
        "focal_length_type": {
            "focal_length_type": "manual",
            "focal_length_values": [26.0] * num_images,
        },
    }
    resp = requests.post(f"{BASE_URL}/v1/avatar/{avatar_id}/process",
                          headers=HEADERS, json=body)
    if resp.status_code == 402:
        raise SystemExit("[person2meta] STOPPED: insufficient credits.")
    resp.raise_for_status()


def wait_until_completed(avatar_id: str, poll_interval_sec: int = 5) -> None:
    url = f"{BASE_URL}/v1/avatar/{avatar_id}/get-status"
    while True:
        resp = requests.get(url, headers={"Authorization": f"Bearer {API_KEY}"})
        resp.raise_for_status()
        status = resp.json().get("status")
        print(f"[person2meta] status: {status}")
        if status == "completed":
            return
        elif status == "failed":
            raise SystemExit("[person2meta] STOPPED: reconstruction failed.")
        time.sleep(poll_interval_sec)


def download_model(avatar_id: str, output_zip_path: str) -> None:
    params = {"mesh_format": "obj", "texture": "jpg"}
    url = f"{BASE_URL}/v1/avatar/{avatar_id}/get-3d-model"
    while True:
        resp = requests.get(url, headers={"Authorization": f"Bearer {API_KEY}"}, params=params)
        resp.raise_for_status()
        result = resp.json()
        event = result.get("event")
        if event == "retry-after":
            time.sleep(result["data"]["time_sec"])
            continue
        elif event == "redirect":
            model_resp = requests.get(result["data"]["url"])
            model_resp.raise_for_status()
            with open(output_zip_path, "wb") as f:
                f.write(model_resp.content)
            return
        else:
            raise RuntimeError(f"Unexpected response: {result}")


def convert_to_fbx(obj_path: str, fbx_path: str) -> None:
    subprocess.run(
        [BLENDER_EXE, "--background", "--python", CONVERT_SCRIPT,
         "--", obj_path, fbx_path],
        check=True,
    )


def main():
    if not API_KEY:
        raise SystemExit("[person2meta] KEENTOOLS_API_KEY not set.")

    os.makedirs(WORK_DIR, exist_ok=True)
    head_name = ask_head_name()

    print("[person2meta] Select reconstruction images...")
    image_paths = pick_images()
    if not (2 <= len(image_paths) <= 15):
        raise SystemExit(f"[person2meta] Selected {len(image_paths)} images, need 2-15.")

    print("[person2meta] Select ONE front-facing portrait for conform...")
    portrait_path = pick_portrait()
    if not portrait_path:
        raise SystemExit("[person2meta] No portrait selected.")

    num_images = len(image_paths)
    zip_path = os.path.join(WORK_DIR, f"{head_name}.zip")
    obj_path = os.path.join(WORK_DIR, f"{head_name}.obj")
    fbx_path = os.path.join(WORK_DIR, f"{head_name}.fbx")

    print("[person2meta] Step 1: init")
    init_data = init_avatar(num_images)
    avatar_id = init_data["avatar_id"]

    print("[person2meta] Step 2: upload")
    upload_images(init_data["img_urls"], image_paths)

    print("[person2meta] Step 3: process (spends a credit)")
    start_processing(avatar_id, num_images)

    print("[person2meta] Step 4: wait for completion")
    wait_until_completed(avatar_id)

    print("[person2meta] Step 5: download model (billed once)")
    download_model(avatar_id, zip_path)

    print("[person2meta] Step 6: unzip")
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(WORK_DIR)
        obj_name = next(n for n in z.namelist() if n.endswith(".obj"))
        extracted_obj_path = os.path.join(WORK_DIR, obj_name)

    print("[person2meta] Step 7: convert OBJ -> FBX via Blender")
    convert_to_fbx(extracted_obj_path, fbx_path)

    print("[person2meta] Step 8: write config for Unreal")
    config = {
        "head_name": head_name,
        "fbx_path": fbx_path,
        "portrait_path": portrait_path,
        "import_destination_path": "/Game/person2meta",
        "imported_mesh_name": f"SM_{head_name}",
        "output_package_path": "/Game/person2meta",
        "output_asset_name": f"MHC_{head_name}",
    }
    with open(CONFIG_OUTPUT_PATH, "w") as f:
        json.dump(config, f, indent=2)

    print(f"[person2meta] Done. Config written to {CONFIG_OUTPUT_PATH}")
    print("[person2meta] Now run conform_to_metahuman.py inside Unreal's Python console.")


if __name__ == "__main__":
    main()
