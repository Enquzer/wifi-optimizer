Set WshShell = CreateObject("WScript.Shell")
rootDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName))
WshShell.CurrentDirectory = rootDir
exePath = rootDir & "\app_bin\WiFi Optimizer\WiFi Optimizer.exe"
If CreateObject("Scripting.FileSystemObject").FileExists(exePath) Then
    WshShell.Run """" & exePath & """", 0, False
Else
    WshShell.Run "pythonw.exe src\main.py", 0, False
End If
