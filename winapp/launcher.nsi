; LiveTranslate.exe — the silent launcher (Windows twin of the macOS bundle's launcher script).
; First run (or --setup): opens the setup page, which installs Python packages + models into %LOCALAPPDATA%\LiveTranslate.
; Afterwards: starts the tray app (server + private AI runtime). Already running: tray.py just opens Control.
Unicode true
SilentInstall silent
RequestExecutionLevel user
Name "LiveTranslate"
OutFile "${OUT}"
Icon "app.ico"
VIProductVersion "${VERNUM}"
VIAddVersionKey "ProductName" "LiveTranslate"
VIAddVersionKey "FileDescription" "LiveTranslate"
VIAddVersionKey "FileVersion" "${VER}"
VIAddVersionKey "ProductVersion" "${VER}"
VIAddVersionKey "LegalCopyright" "LiveTranslate"

!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "env.nsh"

Section
  !insertmacro LT_ENV
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "--setup" $1
  ${If} ${Errors}
  ${AndIf} ${FileExists} "$R0\installed.json"
    ${IfNot} ${FileExists} "$R0\venv\Scripts\pythonw.exe"
      MessageBox MB_ICONSTOP "LiveTranslate is not fully installed. Open it again to finish setup."
      Delete "$R0\installed.json"
      Quit
    ${EndIf}
    Exec '"$R0\venv\Scripts\pythonw.exe" "$EXEDIR\app\tray.py" --launcher "$EXEPATH"'
    Quit
  ${EndIf}

  ; setup: a private Python 3.11 (normally already fetched by the installer), then the setup page
  Banner::show /NOUNLOAD "Preparing LiveTranslate setup…"
  nsExec::Exec '"$EXEDIR\uv.exe" python install 3.11'
  Pop $1
  nsExec::ExecToStack '"$EXEDIR\uv.exe" python find 3.11'
  Pop $1
  Pop $2
  Banner::destroy
  ${If} $1 != 0
    MessageBox MB_ICONSTOP "Setup needs an internet connection the first time, to download its components."
    Quit
  ${EndIf}
  ; "C:\...\python.exe\r\n" -> folder -> pythonw.exe (no console window)
  ${WordFind} "$2" "$\r" "+1" $2
  ${WordFind} "$2" "$\n" "+1" $2
  ${GetParent} "$2" $3
  Exec '"$3\pythonw.exe" "$EXEDIR\app\installer.py" --sup "$R0" --app "$EXEDIR\app" --uv "$EXEDIR\uv.exe" --bundle "$EXEPATH"'
SectionEnd
