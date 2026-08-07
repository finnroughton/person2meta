"""
person2meta - Step 1 prototype
Pops up a window to select images, then launches Blender with a startup
script that will (eventually) create a FaceBuilder head and load the images.

Run this with your regular system Python (not Blender's Python).
"""

import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, simpledialog, ttk
from pathlib import Path

import p2m_settings

# All machine-specific paths now live in one place -- see p2m_settings.py
# (and person2meta_settings.json, created next to it) to edit them.
_settings = p2m_settings.load_settings()
BLENDER_EXE = _settings["blender_launcher_exe"]
UNREAL_PROJECT_PATH = _settings["unreal_project_path"]
UNREAL_EDITOR_EXE = _settings["unreal_editor_exe"]

STARTUP_SCRIPT = str(Path(__file__).parent / "blender_startup.py")

# Must match blender_startup.py's own STATUS_FILE_PATH (both scripts live in
# the same folder, so this resolves to the same file).
STATUS_FILE_PATH = str(Path(__file__).parent / "blender_startup_status.json")
LOADING_OVERLAY_TIMEOUT_SECONDS = 300  # give up waiting and just reveal Blender

# Engine/Binaries/Win64/UnrealEditor.exe -> Engine
ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(UNREAL_EDITOR_EXE)))
ENGINE_PLUGINS_DIR = os.path.join(ENGINE_ROOT, "Plugins")

# The MetaHuman Core plugin set this pipeline actually calls into (conform_to_metahuman.py
# uses unreal.MetaHumanCharacterEditorSubsystem etc, which needs all of these enabled) --
# same set already listed in MyProject2.uproject's own "Plugins" array.
REQUIRED_METAHUMAN_PLUGINS = [
    "MetaHuman",
    "MetaHumanCalibrationProcessing",
    "MetaHumanCoreTech",
    "MetaHumanCharacter",
    "MetaHumanCharacterUAF",
    "MetaHumanLiveLink",
    "MetaHumanRuntime",
    "MetaHumanCalibrationDiagnostics",
]


def _find_plugin_uplugin(name: str):
    """Searches the engine's own Plugins tree for <name>.uplugin (its folder
    location varies -- some ship under Plugins/MetaHuman, some under
    Plugins/Experimental/MetaHuman -- so this doesn't hardcode subfolders).
    Returns the path if found, else None."""
    matches = glob.glob(os.path.join(ENGINE_PLUGINS_DIR, "**", f"{name}.uplugin"), recursive=True)
    return matches[0] if matches else None


def check_metahuman_plugins() -> dict:
    """Returns {"missing_from_engine": [...], "missing_from_project": [...]}.
    "missing_from_engine" means the plugin isn't bundled with this Unreal
    Engine install at all (needs Epic Games Launcher to add/repair -- not
    something this script can fix). "missing_from_project" means the engine
    has it, but MyProject2.uproject doesn't have it enabled yet -- that one
    IS fixable here, by editing the .uproject's Plugins list directly."""
    missing_from_engine = []
    missing_from_project = []

    try:
        with open(UNREAL_PROJECT_PATH, "r") as f:
            uproject = json.load(f)
    except (OSError, json.JSONDecodeError):
        uproject = {"Plugins": []}
    enabled_names = {
        p.get("Name") for p in uproject.get("Plugins", []) if p.get("Enabled")
    }

    for name in REQUIRED_METAHUMAN_PLUGINS:
        if _find_plugin_uplugin(name) is None:
            missing_from_engine.append(name)
        elif name not in enabled_names:
            missing_from_project.append(name)

    return {"missing_from_engine": missing_from_engine, "missing_from_project": missing_from_project}


def _blender_config_dir() -> str | None:
    """Blender's per-version user config dir, e.g.
    %APPDATA%\\Blender Foundation\\Blender\\5.2 -- derived from BLENDER_EXE's
    own folder name ("Blender 5.2") rather than hardcoded, so it stays
    correct if BLENDER_EXE is updated for a different install."""
    match = re.search(r"Blender (\d+\.\d+)", BLENDER_EXE)
    appdata = os.environ.get("APPDATA")
    if not match or not appdata:
        return None
    return os.path.join(appdata, "Blender Foundation", "Blender", match.group(1))


def keentools_installed() -> bool:
    """Checks both places KeenTools could be installed -- as a Blender 4.2+
    extension (the modern default, "bl_ext.user_default.keentools" in
    blender_startup.py's own ensure_facebuilder_enabled) or as a legacy
    scripts/addons install (its older fallback name, plain "keentools")."""
    config_dir = _blender_config_dir()
    if not config_dir:
        return False
    candidates = [
        os.path.join(config_dir, "extensions", "user_default", "keentools"),
        os.path.join(config_dir, "scripts", "addons", "keentools"),
    ]
    return any(os.path.isdir(c) and os.listdir(c) for c in candidates)


def check_requirements() -> dict:
    """Full pre-flight check: Blender itself, the KeenTools add-on, and the
    MetaHuman Core plugin set. Returns:
      {
        "manual_issues": [str, ...],        # can't be fixed by this script
        "missing_from_project": [str, ...]  # MetaHuman plugins this script CAN fix
      }
    Blender and KeenTools are real downloads/installers -- if either is
    missing, that always lands in manual_issues, same as an engine-level
    MetaHuman plugin gap. Only a MetaHuman plugin that's present in the
    engine but just not ticked on for this project is auto-fixable.
    """
    manual_issues = []

    if not os.path.isfile(BLENDER_EXE):
        manual_issues.append(
            f"Blender not found at {BLENDER_EXE}. Install it from blender.org "
            f"(or update BLENDER_EXE in launch_picker.py if it's installed elsewhere)."
        )

    if not keentools_installed():
        manual_issues.append(
            "KeenTools FaceBuilder add-on not found for Blender. Install it "
            "from keentools.io (or via Blender's Get Extensions / "
            "Preferences > Add-ons), then run this again."
        )

    plugin_result = check_metahuman_plugins()
    if plugin_result["missing_from_engine"]:
        names = ", ".join(plugin_result["missing_from_engine"])
        manual_issues.append(
            f"Your Unreal Engine install is missing these MetaHuman plugins: "
            f"{names}. Enable/repair them via the Epic Games Launcher, then "
            f"run this again."
        )

    return {
        "manual_issues": manual_issues,
        "missing_from_project": plugin_result["missing_from_project"],
    }


def enable_plugins_in_uproject(names: list[str]) -> None:
    """Adds/enables the given plugin names in MyProject2.uproject's Plugins
    list and saves. Editing this JSON file directly has the same effect as
    ticking the plugin on in Edit > Plugins inside the editor -- no engine
    reinstall needed since these plugins are already bundled with the
    engine (see check_metahuman_plugins)."""
    with open(UNREAL_PROJECT_PATH, "r") as f:
        uproject = json.load(f)

    plugins = uproject.setdefault("Plugins", [])
    by_name = {p.get("Name"): p for p in plugins}
    for name in names:
        if name in by_name:
            by_name[name]["Enabled"] = True
        else:
            plugins.append({"Name": name, "Enabled": True})

    with open(UNREAL_PROJECT_PATH, "w") as f:
        json.dump(uproject, f, indent="\t")


def show_launcher() -> dict:
    """Shows the entry-point popups: a one-time instructions screen, then
    pick a source type, then a mode.

    "Images" -> "One Head" or "Multiple Heads" both lead somewhere; the
    extra source-type screen/button exists so a video option has an
    obvious place to slot in later. Returns the result dict, with "mode"
    ("one_head", "multi_head", or None if closed without choosing) and,
    for "multi_head", a "heads" list of {"name": str, "images": [str, ...]}.
    """
    root = tk.Tk()
    root.title("person2meta")
    root.resizable(False, False)

    result = {"mode": None}

    def clear():
        for w in root.winfo_children():
            w.destroy()

    def show_instructions_screen():
        clear()
        root.geometry("440x460")
        tk.Label(root, text="Before you start", font=("Segoe UI", 13, "bold")).pack(pady=(18, 8))
        body = (
            "Requires Blender (with the KeenTools FaceBuilder add-on "
            "installed) and Unreal Engine, with the MetaHuman Core plugin "
            "enabled.\n\n"
            "Once Blender opens, each photo is auto-aligned for you "
            "(the same as clicking “Auto Align” in the FaceBuilder panel) "
            "-- just adjust pins as needed.\n\n"
            "When you're happy with it, open the Texture sub-menu, change "
            "the texture dropdown to “MH”, and click “Create "
            "Texture”.\n\n"
            "Then go to the person2meta tab and click “Export & Build "
            "MetaHuman”."
        )
        tk.Label(
            root, text=body, font=("Segoe UI", 10), justify="left", wraplength=390,
        ).pack(padx=24, pady=(0, 16))
        tk.Button(
            root, text="Got it", font=("Segoe UI", 11), width=10,
            command=show_checking_screen,
        ).pack(pady=(0, 16))

    def show_checking_screen():
        clear()
        root.geometry("360x160")
        tk.Label(
            root, text="Checking for MetaHuman installations...",
            font=("Segoe UI", 11), wraplength=320,
        ).pack(pady=(30, 14))
        bar = ttk.Progressbar(root, mode="indeterminate", length=260)
        bar.pack(pady=6)
        bar.start(12)

        def _run_check():
            result = check_requirements()
            root.after(0, lambda: _on_check_done(result))

        threading.Thread(target=_run_check, daemon=True).start()

    def _on_check_done(result: dict):
        if result["manual_issues"]:
            show_manual_issues_screen(result["manual_issues"])
        elif result["missing_from_project"]:
            show_installing_screen(result["missing_from_project"])
        else:
            show_source_screen()

    def show_manual_issues_screen(issues: list[str]):
        clear()
        root.geometry("460x360")
        tk.Label(
            root, text="A few things need installing", font=("Segoe UI", 12, "bold"),
        ).pack(pady=(18, 8))
        body = "\n\n".join(f"• {issue}" for issue in issues)
        tk.Label(root, text=body, font=("Segoe UI", 10), justify="left", wraplength=410).pack(
            padx=24, pady=(0, 16)
        )
        tk.Button(root, text="Close", font=("Segoe UI", 11), width=10, command=root.destroy).pack(
            pady=(0, 16)
        )

    def show_installing_screen(missing_names: list[str]):
        clear()
        root.geometry("380x180")
        tk.Label(
            root, text="Installing MetaHuman plugins", font=("Segoe UI", 12, "bold"),
        ).pack(pady=(20, 4))
        status_var = tk.StringVar(value="Preparing...")
        tk.Label(root, textvariable=status_var, font=("Segoe UI", 10)).pack(pady=(0, 10))
        bar = ttk.Progressbar(root, mode="determinate", length=280, maximum=len(missing_names))
        bar.pack(pady=6)

        def _run_install():
            total = len(missing_names)
            for i, name in enumerate(missing_names, start=1):
                root.after(0, lambda n=name, i=i: (
                    status_var.set(f"Enabling {n}... ({i}/{total})"),
                    bar.configure(value=i),
                ))
                time.sleep(0.35)  # paced so each step is actually readable
            try:
                enable_plugins_in_uproject(missing_names)
            except Exception as e:
                root.after(0, lambda: show_install_error_screen(str(e)))
                return
            root.after(0, lambda: status_var.set("Done!"))
            time.sleep(0.3)
            root.after(0, show_source_screen)

        threading.Thread(target=_run_install, daemon=True).start()

    def show_install_error_screen(message: str):
        clear()
        root.geometry("420x260")
        tk.Label(
            root, text="Couldn't enable plugins", font=("Segoe UI", 12, "bold"),
        ).pack(pady=(18, 8))
        tk.Label(
            root, text=message, font=("Segoe UI", 10), justify="left", wraplength=370,
        ).pack(padx=24, pady=(0, 16))
        tk.Button(root, text="Close", font=("Segoe UI", 11), width=10, command=root.destroy).pack(
            pady=(0, 16)
        )

    def show_source_screen():
        clear()
        root.geometry("320x220")
        tk.Label(root, text="What are you starting from?", font=("Segoe UI", 11)).pack(pady=(20, 10))
        tk.Button(
            root, text="\U0001F5BC️\nImages", font=("Segoe UI", 14),
            width=10, height=4, command=show_mode_screen,
        ).pack(pady=8)

    def show_mode_screen():
        clear()
        root.geometry("320x220")
        tk.Label(root, text="How many heads?", font=("Segoe UI", 11)).pack(pady=(16, 8))
        row = tk.Frame(root)
        row.pack()
        tk.Button(
            row, text="\U0001F9CD\nOne Head", font=("Segoe UI", 14),
            width=10, height=4, command=lambda: choose("one_head"),
        ).pack(side="left", padx=4)
        tk.Button(
            row, text="\U0001F46A\nMultiple Heads", font=("Segoe UI", 12),
            width=10, height=4, command=show_multi_heads_screen,
        ).pack(side="left", padx=4)
        tk.Button(root, text="< Back", command=show_source_screen).pack(pady=(8, 0))

    def show_multi_heads_screen():
        clear()
        root.geometry("420x400")
        heads: list[dict] = result.setdefault("heads", [])

        tk.Label(root, text="Multiple Heads", font=("Segoe UI", 13, "bold")).pack(pady=(16, 4))
        tk.Label(
            root, text="Add a name and photo set for each person.",
            font=("Segoe UI", 10),
        ).pack(pady=(0, 8))

        list_frame = tk.Frame(root)
        list_frame.pack(padx=20, pady=(0, 8), fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        listbox = tk.Listbox(
            list_frame, font=("Segoe UI", 10), yscrollcommand=scrollbar.set, height=8,
        )
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=listbox.yview)

        def refresh_list():
            listbox.delete(0, "end")
            for h in heads:
                listbox.insert("end", f"{h['name']}  ({len(h['images'])} image(s))")

        refresh_list()

        def add_person():
            name = simpledialog.askstring(
                "Person's name", "Name for this head (used to label the MetaHuman):",
                parent=root,
            )
            if not name:
                return
            filetypes = [
                ("Image files", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp"),
                ("All files", "*.*"),
            ]
            paths = filedialog.askopenfilenames(
                title=f"Select face images for {name}", filetypes=filetypes,
            )
            if not paths:
                return
            heads.append({"name": name, "images": list(paths)})
            refresh_list()

        def remove_selected():
            selection = listbox.curselection()
            if not selection:
                return
            del heads[selection[0]]
            refresh_list()

        button_row = tk.Frame(root)
        button_row.pack(pady=(0, 8))
        tk.Button(button_row, text="+ Add Person", command=add_person).pack(side="left", padx=4)
        tk.Button(button_row, text="Remove Selected", command=remove_selected).pack(
            side="left", padx=4
        )

        bottom_row = tk.Frame(root)
        bottom_row.pack(pady=(0, 16))
        tk.Button(
            bottom_row, text="< Back", command=show_mode_screen,
        ).pack(side="left", padx=4)
        tk.Button(
            bottom_row, text="Continue", font=("Segoe UI", 11), width=10,
            command=lambda: choose("multi_head"),
        ).pack(side="left", padx=4)

    def choose(mode: str):
        if mode == "multi_head" and not result.get("heads"):
            return  # nothing added yet -- ignore the click, stay on this screen
        result["mode"] = mode
        root.destroy()

    show_instructions_screen()
    root.mainloop()
    return result


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


def _clear_stale_status() -> None:
    """Deletes any leftover status file from a previous run, so the loading
    overlay below doesn't immediately see a stale done=True and close
    before Blender's even started this time."""
    try:
        os.remove(STATUS_FILE_PATH)
    except OSError:
        pass


def show_loading_overlay() -> None:
    """Full-screen, always-on-top window shown while Blender sets up (loads
    photos, creates heads, auto-aligns them) in the background. Blender's
    own window isn't touched at all -- hiding/minimizing it via OS calls
    was considered, but risked changing how Blender reports viewport
    region sizes, which is exactly the class of "is the area actually
    ready" bug already fought hard to fix elsewhere in this pipeline.
    Covering it with this overlay instead gets the same result (you don't
    see the rapid auto-align pin/camera changes mid-setup, which reads as
    glitching) with zero risk to Blender's own behavior.

    Polls blender_startup.py's STATUS_FILE_PATH and closes itself once that
    reports done=True, or after LOADING_OVERLAY_TIMEOUT_SECONDS regardless
    (so a crash/hang in Blender doesn't strand the user behind this
    window)."""
    root = tk.Tk()
    root.title("person2meta")
    root.attributes("-topmost", True)
    root.attributes("-fullscreen", True)
    root.configure(bg="#1e1e1e")

    tk.Label(
        root, text="Setting up your model(s)...", font=("Segoe UI", 20),
        fg="white", bg="#1e1e1e",
    ).pack(pady=(240, 20))
    status_var = tk.StringVar(value="Starting Blender...")
    tk.Label(
        root, textvariable=status_var, font=("Segoe UI", 12),
        fg="#cccccc", bg="#1e1e1e",
    ).pack(pady=(0, 20))
    bar = ttk.Progressbar(root, mode="indeterminate", length=420)
    bar.pack()
    bar.start(12)
    tk.Label(
        root, text="(Press Esc to skip waiting)", font=("Segoe UI", 9),
        fg="#888888", bg="#1e1e1e",
    ).pack(pady=(24, 0))
    root.bind("<Escape>", lambda event: root.destroy())

    deadline = time.monotonic() + LOADING_OVERLAY_TIMEOUT_SECONDS

    def _poll():
        try:
            with open(STATUS_FILE_PATH, "r") as f:
                status = json.load(f)
            status_var.set(status.get("message", ""))
            if status.get("done"):
                root.destroy()
                return
        except (OSError, json.JSONDecodeError):
            pass

        if time.monotonic() >= deadline:
            root.destroy()
            return
        root.after(300, _poll)

    root.after(300, _poll)
    root.mainloop()


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

    _clear_stale_status()
    subprocess.Popen(
        [
            BLENDER_EXE,
            "--python", STARTUP_SCRIPT,
        ],
        env=env,
    )
    show_loading_overlay()


def launch_blender_with_multiple_heads(heads: list[dict]) -> None:
    """Launches Blender, passing all the named head definitions to the
    startup script through P2M_MULTI_HEADS (JSON) -- see
    blender_startup.py's create_multiple_heads for how it's consumed."""
    if not heads:
        print("No heads defined. Aborting.")
        return

    env = os.environ.copy()
    env["P2M_MULTI_HEADS"] = json.dumps(heads)

    print(f"Defined {len(heads)} head(s). Launching Blender...")
    for h in heads:
        print(f"  - {h['name']}: {len(h['images'])} image(s)")

    _clear_stale_status()
    subprocess.Popen(
        [
            BLENDER_EXE,
            "--python", STARTUP_SCRIPT,
        ],
        env=env,
    )
    show_loading_overlay()


if __name__ == "__main__":
    launch_result = show_launcher()
    mode = launch_result.get("mode")
    if mode == "one_head":
        images = pick_images()
        launch_blender_with_images(images)
    elif mode == "multi_head":
        launch_blender_with_multiple_heads(launch_result.get("heads", []))
    else:
        print("No option selected. Aborting.")
