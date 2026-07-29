"""
person2meta - Step 1 prototype
Pops up a window to select images, then launches Blender with a startup
script that will (eventually) create a FaceBuilder head and load the images.

Run this with your regular system Python (not Blender's Python).
"""

import subprocess
import sys
import tkinter as tk
from tkinter import filedialog
from pathlib import Path

# ---- CONFIG: edit these two paths for your machine ----
BLENDER_EXE = r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe"  # adjust to your install
STARTUP_SCRIPT = str(Path(__file__).parent / "blender_startup.py")
# --------------------------------------------------------


def pick_images() -> list[str]:
    """Opens a simple file-picker window and returns selected image paths."""
    root = tk.Tk()
    root.withdraw()  # hide the empty root window, we only want the dialog

    filetypes = [
        ("Image files", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"),
        ("All files", "*.*"),
    ]

    paths = filedialog.askopenfilenames(
        title="Select face images",
        filetypes=filetypes,
    )

    root.destroy()
    return list(paths)


def launch_blender_with_images(image_paths: list[str]) -> None:
    """Launches Blender, passing image paths to the startup script via env var."""
    if not image_paths:
        print("No images selected. Aborting.")
        return

    # Pass the image list to the Blender-side script through an environment
    # variable, since command-line args after '--' are also an option but
    # env vars are simpler to parse reliably across paths with spaces.
    import os
    env = os.environ.copy()
    env["P2M_IMAGE_PATHS"] = "||".join(image_paths)  # simple delimiter, avoids comma-in-path issues

    print(f"Selected {len(image_paths)} image(s). Launching Blender...")
    for p in image_paths:
        print(f"  - {p}")

    subprocess.Popen(
        [
            "C:\Program Files\Blender Foundation\Blender 5.2\blender-launcher.exe",
            "--python", STARTUP_SCRIPT,
        ],
        env=env,
    )


if __name__ == "__main__":
    images = pick_images()
    launch_blender_with_images(images)
