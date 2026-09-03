; Pipedeck NSIS installer hooks (Tauri bundle.windows.nsis.installerHooks)
; 安装后把 CLI 目录写入用户 PATH 并广播 WM_SETTINGCHANGE；卸载时仅移除指向本安装目录的条目。
; PowerShell 负责去重与广播（SetEnvironmentVariable User 作用域自动通知）。

!macro _PIPEDCK_BIN_DIR
  "$INSTDIR\resources\bin"
!macroend

!macro NSIS_HOOK_PREINSTALL
!macroend

!macro NSIS_HOOK_POSTINSTALL
  Var /GLOBAL PIPEDCK_BINDIR
  StrCpy $PIPEDCK_BINDIR "$INSTDIR\resources\bin"
  nsExec::ExecToLog `powershell -NoProfile -ExecutionPolicy Bypass -Command "$$d='$PIPEDCK_BINDIR'; $$p=[Environment]::GetEnvironmentVariable('Path','User'); if([string]::IsNullOrEmpty($$p)){[Environment]::SetEnvironmentVariable('Path',$$d,'User')}elseif(($$p -split ';') -notcontains $$d){[Environment]::SetEnvironmentVariable('Path', ($$p.TrimEnd(';') + ';' + $$d), 'User')}"`
  Pop $0
  DetailPrint "Pipedeck CLI PATH 注册完成（$0）"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  StrCpy $PIPEDCK_BINDIR "$INSTDIR\resources\bin"
  nsExec::ExecToLog `powershell -NoProfile -ExecutionPolicy Bypass -Command "$$d='$PIPEDCK_BINDIR'; $$p=[Environment]::GetEnvironmentVariable('Path','User'); if(($$p -split ';') -contains $$d){$$n=($$p -split ';' | Where-Object { $$_ -ne $$d }) -join ';'; [Environment]::SetEnvironmentVariable('Path', $$n, 'User')}"`
  Pop $0
  DetailPrint "Pipedeck CLI PATH 已清理（$0）"
!macroend
