' Asset Studio — smart one-click launcher.
'  • First run on a PC: detects missing pieces and runs setup (visible window).
'  • Every run after: opens the app silently (it lives in your system tray).
' Move/copy the whole STUDIO folder anywhere — this finds everything relatively.
Set fso = CreateObject("Scripting.FileSystemObject")
base     = fso.GetParentFolderName(WScript.ScriptFullName)
frontend = fso.BuildPath(base, "frontend")
backend  = fso.BuildPath(base, "backend")
electron = fso.BuildPath(frontend, "node_modules\electron\dist\electron.exe")
venvPy   = fso.BuildPath(backend,  ".venv\Scripts\python.exe")
nodeMods = fso.BuildPath(frontend, "node_modules")
distIdx  = fso.BuildPath(frontend, "dist\index.html")
setup    = fso.BuildPath(base, "setup.ps1")

Set sh = CreateObject("WScript.Shell")

needSetup = (Not fso.FileExists(venvPy)) Or (Not fso.FolderExists(nodeMods)) _
         Or (Not fso.FileExists(distIdx)) Or (Not fso.FileExists(electron))

If needSetup Then
  MsgBox "First-time setup needed on this PC." & vbCrLf & vbCrLf & _
         "A window will open and install everything (a few minutes)." & vbCrLf & _
         "The studio opens automatically when it's done.", 64, "Asset Studio"
  ' run setup visibly (window style 1) and WAIT for it to finish
  sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -File """ & setup & """", 1, True
  If Not fso.FileExists(electron) Or Not fso.FileExists(distIdx) Then
    MsgBox "Setup didn't finish successfully." & vbCrLf & _
           "Make sure Python 3.10+ and Node.js are installed, then try again." & vbCrLf & vbCrLf & _
           "You can also run 'setup.ps1' manually (right-click -> Run with PowerShell).", 48, "Asset Studio"
    WScript.Quit
  End If
End If

' launch the app hidden (Electron shows its own window + tray)
sh.CurrentDirectory = frontend
sh.Run """" & electron & """ """ & frontend & """", 0, False
