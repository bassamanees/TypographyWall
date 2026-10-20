# Typography Wall - installer for Windows (64-bit).
# Downloads the latest release from GitHub, installs it for the current user
# (no administrator needed), adds Desktop and Start-menu shortcuts, and opens it.
#
# One-line install (paste into PowerShell):
#   irm https://raw.githubusercontent.com/bassamanees/TypographyWall/main/install_windows.ps1 | iex

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # much faster downloads in Windows PowerShell 5.1
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo  = 'bassamanees/TypographyWall'
$Asset = 'TypographyWall-windows.exe'
$Url   = "https://github.com/$Repo/releases/latest/download/$Asset"

Write-Host '== Typography Wall installer ==' -ForegroundColor Cyan

if (-not [Environment]::Is64BitOperatingSystem) {
    Write-Host 'Sorry: Typography Wall needs 64-bit Windows.' -ForegroundColor Red
    return
}

$Dest = Join-Path $env:LOCALAPPDATA 'Programs\TypographyWall'
$Exe  = Join-Path $Dest 'TypographyWall.exe'
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

# Close a running copy so the file can be replaced
Get-Process TypographyWall -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host 'Downloading the latest version (about 150 MB)...'
Invoke-WebRequest -Uri $Url -OutFile "$Exe.download" -UseBasicParsing
Move-Item -Force "$Exe.download" $Exe

# The app is not code-signed; removing the "downloaded from the internet" mark
# stops the blue SmartScreen warning for this copy.
Unblock-File -Path $Exe

Write-Host 'Creating shortcuts...'
$Shell = New-Object -ComObject WScript.Shell
$Links = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Typography Wall.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Typography Wall.lnk')
)
foreach ($Path in $Links) {
    $Link = $Shell.CreateShortcut($Path)
    $Link.TargetPath = $Exe
    $Link.WorkingDirectory = $Dest
    $Link.IconLocation = "$Exe,0"
    $Link.Save()
}

Write-Host "Done. Installed to $Dest" -ForegroundColor Green
Write-Host 'Opening Typography Wall (first start takes a few seconds)...'
Start-Process -FilePath $Exe -WorkingDirectory $Dest
