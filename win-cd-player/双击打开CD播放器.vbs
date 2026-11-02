' Discflow launcher: run Discflow.exe if present, otherwise run main.py with pythonw (no console)
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
exePath = scriptDir & "\Discflow.exe"
If fso.FileExists(exePath) Then
    CreateObject("WScript.Shell").Run """" & exePath & """", 1, False
Else
    CreateObject("WScript.Shell").Run "pythonw.exe """ & scriptDir & "\main.py""", 1, False
End If
