; RedactGate Desktop — Windows installer (NSIS, per-user, no admin rights needed)
Unicode true
!include "MUI2.nsh"
!define APPNAME "RedactGate Desktop"
!ifndef VERSION
  !define VERSION "0.1.0"
!endif
Name "${APPNAME}"
OutFile "${OUTDIR}\RedactGate-Desktop-Setup-${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\RedactGate Desktop"
RequestExecutionLevel user
SetCompressor /SOLID lzma
!define MUI_ICON "icons\icon.ico"
!define MUI_UNICON "icons\icon.ico"
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${LICENSEFILE}"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\RedactGate Desktop.exe"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Ukrainian"
!insertmacro MUI_LANGUAGE "Russian"
!insertmacro MUI_LANGUAGE "English"
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=${LANG_ENGLISH} "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "FileDescription" "${APPNAME} installer"
VIAddVersionKey /LANG=${LANG_ENGLISH} "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "LegalCopyright" "AGPL-3.0-or-later"
VIAddVersionKey /LANG=${LANG_ENGLISH} "CompanyName" "INNOVA CONSULT LTD"

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "${SRCDIR}\*.*"
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\RedactGate Desktop.exe"
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\RedactGate Desktop.exe"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RedactGateDesktop" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RedactGateDesktop" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RedactGateDesktop" "Publisher" "INNOVA CONSULT LTD"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RedactGateDesktop" "DisplayIcon" "$INSTDIR\RedactGate Desktop.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RedactGateDesktop" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RedactGateDesktop" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RedactGateDesktop" "NoRepair" 1
SectionEnd

Section "Uninstall"
  ; the encrypted vault in %APPDATA%\RedactGate Desktop is kept on purpose (it holds the label dictionary)
  RMDir /r "$INSTDIR"
  Delete "$DESKTOP\${APPNAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APPNAME}"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RedactGateDesktop"
SectionEnd
