; LiveTranslate Setup <date>.exe — the file to hand out. Per-user install (no administrator rights needed):
;   program  -> %LOCALAPPDATA%\Programs\LiveTranslate      (code, uv, launcher; ~20 MB)
;   models   -> %LOCALAPPDATA%\LiveTranslate               (downloaded by the setup page; kept on update)
; Finish page opens LiveTranslate, whose setup page downloads speech/translation/voice/AI models once.
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "env.nsh"

Name "LiveTranslate"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\LiveTranslate"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "LiveTranslate ${VER}"
VIProductVersion "${VERNUM}"
VIAddVersionKey "ProductName" "LiveTranslate"
VIAddVersionKey "FileDescription" "LiveTranslate Setup"
VIAddVersionKey "FileVersion" "${VER}"
VIAddVersionKey "ProductVersion" "${VER}"
VIAddVersionKey "LegalCopyright" "LiveTranslate"

!define MUI_ICON "app.ico"
!define MUI_UNICON "app.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "LiveTranslate"
!define MUI_WELCOMEPAGE_TEXT "Live speech translation and subtitles for events — runs entirely on this computer.$\r$\n$\r$\nThis installs the LiveTranslate app (no administrator rights needed). Then a setup page opens in your browser to download the speech, translation and voice models once (1–12 GB depending on your choices). After that, no internet is needed.$\r$\n$\r$\nRecommended: Windows 10/11 64-bit, 16 GB of memory, an NVIDIA graphics card (RTX 3060 or better) for the most accurate speech recognition. Without one it runs on the processor with a smaller model."
!define MUI_FINISHPAGE_TITLE "LiveTranslate is installed"
!define MUI_FINISHPAGE_TEXT "Next: the setup page downloads the models (keep this computer online for that).$\r$\n$\r$\nWhen Windows asks whether pythonw may use the network, allow it on Private networks — phones, vMix and OBS connect to LiveTranslate over the local network."
!define MUI_FINISHPAGE_RUN "$INSTDIR\LiveTranslate.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Open LiveTranslate setup now"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

!define UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\LiveTranslate"

; quit a running copy so the new code is what runs next (its processes run from the models folder's Python)
!macro STOP_RUNNING
  nsExec::Exec `powershell -NoProfile -NonInteractive -Command "Get-CimInstance Win32_Process | Where-Object { $$_.CommandLine -like '*LiveTranslate*' -and $$_.ProcessId -ne $$PID -and ($$_.Name -like 'python*' -or $$_.Name -like 'ollama*' -or $$_.Name -like 'msedge*') } | ForEach-Object { Stop-Process -Id $$_.ProcessId -Force -ErrorAction SilentlyContinue }"`
  Pop $0
!macroend

Section "LiveTranslate"
  DetailPrint "Closing LiveTranslate if it is running…"
  !insertmacro STOP_RUNNING
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\app"  ; no stale files from an older version
  File /r "stage\*"
  WriteUninstaller "$INSTDIR\Uninstall LiveTranslate.exe"

  CreateShortcut "$SMPROGRAMS\LiveTranslate.lnk" "$INSTDIR\LiveTranslate.exe" "" "$INSTDIR\LiveTranslate.exe" 0
  CreateShortcut "$DESKTOP\LiveTranslate.lnk" "$INSTDIR\LiveTranslate.exe" "" "$INSTDIR\LiveTranslate.exe" 0
  WriteRegStr HKCU "${UNINST}" "DisplayName" "LiveTranslate"
  WriteRegStr HKCU "${UNINST}" "DisplayVersion" "${VER}"
  WriteRegStr HKCU "${UNINST}" "Publisher" "LiveTranslate"
  WriteRegStr HKCU "${UNINST}" "DisplayIcon" "$INSTDIR\LiveTranslate.exe"
  WriteRegStr HKCU "${UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST}" "UninstallString" '"$INSTDIR\Uninstall LiveTranslate.exe"'
  WriteRegDWORD HKCU "${UNINST}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST}" "NoRepair" 1
  WriteRegDWORD HKCU "${UNINST}" "EstimatedSize" 30000

  ; a private Python 3.11 now, while this window shows progress (the setup page needs it to start)
  !insertmacro LT_ENV
  DetailPrint "Downloading Python for LiveTranslate (one time, ~30 MB)…"
  nsExec::ExecToLog '"$INSTDIR\uv.exe" python install 3.11'
  Pop $0
  ${If} $0 != 0
    DetailPrint "Could not download Python now — it will be retried when LiveTranslate opens."
  ${EndIf}
SectionEnd

Section "Uninstall"
  !insertmacro STOP_RUNNING
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "LiveTranslate"
  DeleteRegKey HKCU "${UNINST}"
  Delete "$SMPROGRAMS\LiveTranslate.lnk"
  Delete "$DESKTOP\LiveTranslate.lnk"
  RMDir /r "$INSTDIR"
  MessageBox MB_YESNO|MB_ICONQUESTION "Also delete the downloaded models, saved setups and transcripts?$\r$\n($LOCALAPPDATA\LiveTranslate — several GB)$\r$\n$\r$\nChoose No to keep them for a later reinstall." /SD IDNO IDNO keep
    RMDir /r "$LOCALAPPDATA\LiveTranslate"
  keep:
SectionEnd
