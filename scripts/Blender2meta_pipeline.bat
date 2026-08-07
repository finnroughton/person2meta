@echo off
REM Blender2meta_pipeline.bat
REM =========================
REM One-click entry point for the person2meta pipeline's Blender half:
REM pick face photos -> Blender opens with a FaceBuilder head built and
REM auto-fit from them -> tweak pins if you want -> click "Export & Build
REM MetaHuman" in the person2meta tab, which exports the head, bakes a
REM texture, and launches Unreal to conform + build the MetaHuman
REM automatically (conform_to_metahuman.py).
REM
REM Just double-click this file. Nothing to edit -- launch_picker.py and
REM blender_startup.py already know where Blender/Unreal/the project live.

python "%~dp0launch_picker.py"
pause
