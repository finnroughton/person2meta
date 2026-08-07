' Runs Blender2meta_pipeline.bat with its console window hidden.
' The .bat itself still works fine double-clicked directly (useful for
' debugging -- you'll see console output/errors that way). This wrapper
' is only for the desktop shortcut, which wants a clean launch with no
' black window flashing up.
Set objShell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
objShell.Run """" & scriptDir & "\Blender2meta_pipeline.bat""", 0, False
