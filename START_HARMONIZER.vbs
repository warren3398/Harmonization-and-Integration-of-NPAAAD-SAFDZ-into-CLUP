Option Explicit
Dim shell, fso, base, runBat, installBat
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
runBat = Chr(34) & base & "\RUN_WEBSITE.bat" & Chr(34)
installBat = Chr(34) & base & "\INSTALL_AND_RUN.bat" & Chr(34)
If fso.FileExists(base & "\.venv\Scripts\python.exe") Then
    shell.Run runBat, 0, False
Else
    ' First-time setup is shown so installation errors are visible.
    shell.Run installBat, 1, False
End If
