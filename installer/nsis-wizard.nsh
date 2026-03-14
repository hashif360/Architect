; Architect NSIS Installer Customization
; Adds a "Cursor Dependency Check" page to the installer wizard

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"

Var CursorFound
Var CursorDialog
Var CursorLabel
Var CursorStatusLabel
Var CursorDownloadLink

; Custom page for Cursor dependency check
Function cursorCheckPage
  nsDialogs::Create 1018
  Pop $CursorDialog

  ${If} $CursorDialog == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 30u "Architect uses Cursor for AI-powered features."
  Pop $CursorLabel

  ${NSD_CreateLabel} 0 35u 100% 20u "Checking for Cursor installation..."
  Pop $CursorStatusLabel

  ; Check common Cursor install locations
  StrCpy $CursorFound "0"

  IfFileExists "$LOCALAPPDATA\Programs\cursor\Cursor.exe" 0 +3
    StrCpy $CursorFound "1"
    Goto CursorCheckDone

  IfFileExists "$PROGRAMFILES\Cursor\Cursor.exe" 0 +3
    StrCpy $CursorFound "1"
    Goto CursorCheckDone

  IfFileExists "$PROGRAMFILES64\Cursor\Cursor.exe" 0 CursorCheckDone
    StrCpy $CursorFound "1"

  CursorCheckDone:

  ${If} $CursorFound == "1"
    ${NSD_SetText} $CursorStatusLabel "Cursor is installed. AI features will be available."
    SetCtlColors $CursorStatusLabel 0x22C55E transparent
  ${Else}
    ${NSD_SetText} $CursorStatusLabel "Cursor was not detected. AI features will be limited."
    SetCtlColors $CursorStatusLabel 0xF59E0B transparent

    ${NSD_CreateLabel} 0 60u 100% 20u "You can install Cursor later from: https://cursor.com"
    Pop $CursorDownloadLink

    ${NSD_CreateLabel} 0 85u 100% 30u "Architect will work without Cursor, but the AI Chat panel will not be functional until Cursor is installed."
    Pop $0
  ${EndIf}

  nsDialogs::Show
FunctionEnd

Function cursorCheckPageLeave
  ; Nothing to validate, allow proceeding regardless
FunctionEnd

; Register the custom page
!macro customHeader
  !insertmacro MUI_PAGE_WELCOME
  !insertmacro MUI_PAGE_LICENSE "${BUILD_RESOURCES_DIR}\license.txt"
  Page custom cursorCheckPage cursorCheckPageLeave
!macroend

; Add PATH option
!macro customInstall
  ; Add architect CLI to user PATH
  EnVar::SetHKCU
  EnVar::AddValue "PATH" "$INSTDIR"

  ; Create file association for .architect files
  WriteRegStr HKCU "Software\Classes\.architect" "" "ArchitectProject"
  WriteRegStr HKCU "Software\Classes\ArchitectProject" "" "Architect Project"
  WriteRegStr HKCU "Software\Classes\ArchitectProject\DefaultIcon" "" "$INSTDIR\Architect.exe,0"
  WriteRegStr HKCU "Software\Classes\ArchitectProject\shell\open\command" "" '"$INSTDIR\Architect.exe" "%1"'
!macroend

!macro customUnInstall
  ; Remove from PATH
  EnVar::SetHKCU
  EnVar::DeleteValue "PATH" "$INSTDIR"

  ; Remove file association
  DeleteRegKey HKCU "Software\Classes\.architect"
  DeleteRegKey HKCU "Software\Classes\ArchitectProject"
!macroend
