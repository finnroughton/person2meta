# person2meta

Turns uploaded photos/video into a MetaHuman-ready 3D head, using KeenTools
FaceBuilder for reconstruction and Epic's Mesh to MetaHuman for the final rig.

**Status: early prototype / internal shell.** Not yet functional end-to-end —
see "Current state" below for exactly what works and what's still stubbed out.

## Repo layout

```
person2meta/
├── Plugins/
│   └── person2meta/          Unreal Engine plugin skeleton (UE 5.7)
│       ├── person2meta.uplugin
│       └── Source/person2meta/
├── scripts/
│   ├── launch_picker.py      Runs on your system Python. Opens a file-picker
│   │                         window for images, then launches Blender.
│   └── blender_startup.py    Runs inside Blender. Will eventually create a
│                             FaceBuilder head and load the selected images.
└── README.md
```

## Current state

- **Unreal plugin**: empty shell. Loads cleanly in the editor (Edit > Plugins
  should show "Person2Meta"), logs on startup/shutdown, no functionality wired
  up yet.
- **launch_picker.py**: working. Pops up a native file dialog, passes selected
  image paths to Blender via an environment variable, launches Blender.
- **blender_startup.py**: placeholder only. Prints the received image paths to
  Blender's console but does NOT yet call FaceBuilder to create a head or load
  images. The real FaceBuilder operator IDs need to be found manually (enable
  Developer Extras in Blender, right-click FaceBuilder's "Create New Head" /
  "Add Images" buttons, "Copy Python Command") and dropped into the placeholder
  function in that file.

## Planned pipeline (target flow)

1. User drops in photos or a video.
2. If video, extract ~6 frames.
3. Launch Blender with those images pre-loaded into a new FaceBuilder head.
4. User manually tweaks the head in Blender (human checkpoint — required,
   since FaceBuilder's alignment isn't reliably automatable).
5. On a custom "Send to Unreal" button, export FBX and hand off to Unreal.
6. Unreal opens, mesh gets imported and run through MetaHuman Identity /
   Mesh to MetaHuman (second human checkpoint — Epic's cloud conform step
   requires an authenticated Epic account and cannot be automated away).
7. Rigged, animate-ready MetaHuman.

## Requirements

- Blender with KeenTools FaceBuilder installed and licensed
- Unreal Engine 5.7
- Python 3.x (for `launch_picker.py`, run with your system Python — not
  Blender's bundled Python)

## Setup

1. Edit `BLENDER_EXE` at the top of `scripts/launch_picker.py` to point at
   your local Blender install.
2. Copy `Plugins/person2meta` into your Unreal project's own `Plugins/`
   folder to load the plugin skeleton.
