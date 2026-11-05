@echo off
setlocal
cd /d "%~dp0"
title BOOM REVIEW - FIX TTS WINERROR 206
powershell -NoProfile -Command "if (([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { exit 0 } else { exit 1 }"
if errorlevel 1 (
 echo Hay bam chuot phai file nay va chon Run as administrator.
 pause
 exit /b 1
)
echo Hay dong BoomReview truoc khi tiep tuc.
pause
if not exist "LongPathsEnabled_before.reg" (
 powershell -NoProfile -Command "$p='HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem'; $v=Get-ItemProperty -LiteralPath $p -Name LongPathsEnabled -ErrorAction SilentlyContinue; $q=[string][char]34; $line=if($null -eq $v){$q+'LongPathsEnabled'+$q+'=-'}else{$q+'LongPathsEnabled'+$q+'=dword:'+('{0:x8}' -f [uint32]$v.LongPathsEnabled)}; @('Windows Registry Editor Version 5.00','','[HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\FileSystem]',$line) | Set-Content -LiteralPath 'LongPathsEnabled_before.reg' -Encoding Unicode"
 if errorlevel 1 goto failed
)
reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f
if errorlevel 1 goto failed
reg query "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled
echo.
echo DA BAT HO TRO DUONG DAN DAI. Hay khoi dong lai Windows roi thu lai TTS.
echo Neu van loi: chon thu muc xuat C:\BR va ten phim ngan, vi du Phim01.
echo File nay khong thay the hoac cap nhat BoomReview.exe.
echo Phuc hoi: nhap file LongPathsEnabled_before.reg neu can.
pause
exit /b 0
:failed
echo KHONG AP DUNG DUOC. Hay gui anh cua so nay cho nguoi ho tro.
pause
exit /b 1

