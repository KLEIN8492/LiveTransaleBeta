; Environment shared by the launcher and the installer: everything LiveTranslate downloads lives in ONE folder,
; %LOCALAPPDATA%\LiveTranslate (delete it to remove every model). Child processes inherit these variables.
!include "WordFunc.nsh"
!macro LT_SET name value
  System::Call 'Kernel32::SetEnvironmentVariable(t "${name}", t "${value}")i'
!macroend
!macro LT_ENV
  StrCpy $R0 "$LOCALAPPDATA\LiveTranslate"
  CreateDirectory "$R0\logs"
  !insertmacro LT_SET "LT_SUPPORT" "$R0"
  !insertmacro LT_SET "LT_DATA" "$R0"
  !insertmacro LT_SET "HF_HOME" "$R0\hf"
  !insertmacro LT_SET "XDG_DATA_HOME" "$R0\xdg\data"
  !insertmacro LT_SET "XDG_CACHE_HOME" "$R0\xdg\cache"
  !insertmacro LT_SET "XDG_CONFIG_HOME" "$R0\xdg\config"
  !insertmacro LT_SET "UV_PYTHON_INSTALL_DIR" "$R0\python"
  !insertmacro LT_SET "UV_CACHE_DIR" "$R0\uv-cache"
  !insertmacro LT_SET "UV_PYTHON_PREFERENCE" "only-managed"
  !insertmacro LT_SET "OLLAMA_MODELS" "$R0\ollama\models"
  !insertmacro LT_SET "PYTHONIOENCODING" "utf-8"
  !insertmacro LT_SET "PYTHONUTF8" "1"
!macroend
