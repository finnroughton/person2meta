"""
person2meta - Audio/Video prompt popup, launched from Blender's "Export &
Build MetaHuman" button chain.

Blender's own bpy.types.Operator + window_manager.popup_menu chain turned
out to be unreliable for a popup nested inside another popup's button click
(opening a second popup_menu from there was silently swallowed, even with a
deferred bpy.app.timers retry). A real Tkinter window -- the same approach
already used for the "Images" -> "One Head" launcher popups in
launch_picker.py -- sidesteps that entirely, since it's a normal top-level
window, not a Blender UI popup.

Run with regular system Python (needs tkinter, which Blender's own bundled
Python does NOT ship with -- that's why this runs as a separate process,
launched via subprocess from blender_startup.py, same pattern as
launch_picker.py launching Blender itself).

Usage: python audio_video_prompt.py <result_json_path>
Writes one of:
  {"choice": "no"}
  {"choice": "audio", "files": [...]}
  {"choice": "video", "files": [...]}
  {"choice": "both",  "files": [...]}
  {"choice": "cancelled"}   -- window closed without finishing
"""

import json
import sys
import tkinter as tk
from tkinter import filedialog


def main():
    if len(sys.argv) < 2:
        print("Usage: audio_video_prompt.py <result_json_path>")
        sys.exit(1)
    result_path = sys.argv[1]

    result = {"choice": "cancelled"}

    root = tk.Tk()
    root.title("person2meta")
    root.geometry("320x220")
    root.resizable(False, False)

    def clear():
        for w in root.winfo_children():
            w.destroy()

    def finish(choice, files=None):
        result["choice"] = choice
        if files is not None:
            result["files"] = list(files)
        root.destroy()

    def show_yes_no_screen():
        clear()
        tk.Label(root, text="Add audio or video?", font=("Segoe UI", 11)).pack(pady=(24, 14))
        row = tk.Frame(root)
        row.pack()
        tk.Button(row, text="Yes", font=("Segoe UI", 12), width=8, height=2,
                  command=show_kind_screen).pack(side="left", padx=8)
        tk.Button(row, text="No", font=("Segoe UI", 12), width=8, height=2,
                  command=lambda: finish("no")).pack(side="left", padx=8)

    def pick_files_for(kind: str):
        filetypes = {
            "audio": [("Audio files", "*.mp3 *.wav *.m4a *.aac *.flac"), ("All files", "*.*")],
            "video": [("Video files", "*.mp4 *.mov *.avi *.mkv"), ("All files", "*.*")],
            "both": [
                ("Audio/Video files", "*.mp3 *.wav *.m4a *.aac *.flac *.mp4 *.mov *.avi *.mkv"),
                ("All files", "*.*"),
            ],
        }[kind]
        paths = filedialog.askopenfilenames(title=f"Select {kind} file(s)", filetypes=filetypes)
        if paths:
            finish(kind, paths)
        else:
            show_kind_screen()  # dialog cancelled -- let them pick a different kind

    def show_kind_screen():
        clear()
        tk.Label(root, text="Audio, video, or both?", font=("Segoe UI", 11)).pack(pady=(16, 10))
        tk.Button(root, text="\U0001F3A7 Audio", font=("Segoe UI", 12), width=14,
                  command=lambda: pick_files_for("audio")).pack(pady=4)
        tk.Button(root, text="\U0001F3AC Video", font=("Segoe UI", 12), width=14,
                  command=lambda: pick_files_for("video")).pack(pady=4)
        tk.Button(root, text="\U0001F3A7\U0001F3AC Both", font=("Segoe UI", 12), width=14,
                  command=lambda: pick_files_for("both")).pack(pady=4)
        tk.Button(root, text="< Back", command=show_yes_no_screen).pack(pady=(8, 0))

    show_yes_no_screen()
    root.mainloop()

    with open(result_path, "w") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
