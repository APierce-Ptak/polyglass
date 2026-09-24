# Polyglass uninstaller. Start it by double-clicking Uninstall.bat.
# Removes everything Install.bat set up, except this folder's own files
# (a script can't delete the folder it runs from).
#   -Yes   don't ask; remove everything except the Windows OCR packs
param([switch]$Yes)

$app = $PSScriptRoot
$venv = Join-Path $app '.venv'
$dataRoot = if ($env:XDG_DATA_HOME) { $env:XDG_DATA_HOME } else { Join-Path $HOME '.local\share' }
$cacheRoot = if ($env:XDG_CACHE_HOME) { $env:XDG_CACHE_HOME } else { Join-Path $HOME '.local\cache' }
$models = Join-Path $dataRoot 'argos-translate'
$modelCache = Join-Path $cacheRoot 'argos-translate'
$shortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Polyglass.lnk'
$ocrList = Join-Path $app 'ocr_added.txt'
$files = 'polyglass.json', 'polyglass.log', 'setup_wizard.log', '__pycache__' | ForEach-Object { Join-Path $app $_ }

function SizeOf($path) {
    if (-not (Test-Path -LiteralPath $path)) { return 0 }
    $sum = (Get-ChildItem -LiteralPath $path -Recurse -Force -File -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
    if ($sum) { $sum } else { 0 }
}

function Show($bytes) {
    if ($bytes -ge 1GB) { '{0:N1} GB' -f ($bytes / 1GB) } else { '{0:N0} MB' -f ($bytes / 1MB) }
}

function Ask($question) {
    if ($Yes) { return $true }
    (Read-Host "$question (y/N)") -match '^\s*y'
}

Write-Host ''
Write-Host 'Polyglass uninstaller' -ForegroundColor Cyan
Write-Host ''
$items = @(
    @{ Label = 'App packages (.venv)'; Path = $venv },
    @{ Label = 'Translation models'; Path = $models },
    @{ Label = 'Model download cache'; Path = $modelCache },
    @{ Label = 'Desktop shortcut'; Path = $shortcut }
) | Where-Object { Test-Path -LiteralPath $_.Path }

$total = 0
foreach ($i in $items) {
    $size = SizeOf $i.Path
    $total += $size
    Write-Host ('  {0,-24} {1,9}   {2}' -f $i.Label, (Show $size), $i.Path)
}
if (-not $items) {
    Write-Host '  Nothing to remove: Polyglass does not look installed.'
}
Write-Host ''
if (Test-Path -LiteralPath $models) {
    Write-Host 'Note: the translation models folder is shared with other Argos Translate apps' -ForegroundColor DarkGray
    Write-Host '(such as LibreTranslate), if you have any.' -ForegroundColor DarkGray
    Write-Host ''
}

if ($items) {
    if (-not (Ask "Remove all of this ($(Show $total))?")) {
        Write-Host 'Nothing was removed.'
        exit 0
    }

    # Close Polyglass if it's running from this folder.
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($venv, [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500

    foreach ($p in @($items.Path) + $files) {
        if (Test-Path -LiteralPath $p) {
            Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue
            if (Test-Path -LiteralPath $p) { Write-Host "  Could not remove $p" -ForegroundColor Yellow }
            else { Write-Host "  Removed $p" }
        }
    }
}

# Windows OCR packs: only the ones Install.bat added, and only if asked (needs admin).
if (Test-Path -LiteralPath $ocrList) {
    $packs = @(Get-Content -LiteralPath $ocrList | Where-Object { $_ -match '^Language\.OCR~~~[\w-]+~~~[\d.]+$' } | Select-Object -Unique)
    if ($packs -and -not $Yes) {
        Write-Host ''
        Write-Host 'Install.bat added these Windows text-recognition (OCR) packs:'
        $packs | ForEach-Object { Write-Host "  $($_.Split('~')[3])" }
        Write-Host 'They are small, and other apps can use them too.'
        if (Ask 'Remove them as well? Windows will ask for permission') {
            $script = Join-Path $env:TEMP 'polyglass_remove_ocr.ps1'
            $names = ($packs | ForEach-Object { "'$_'" }) -join ','
            Set-Content -LiteralPath $script -Encoding UTF8 -Value (
                "foreach (`$n in @($names)) { Remove-WindowsCapability -Online -Name `$n | Out-Null }")
            try {
                Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden `
                    -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$script`""
                Remove-Item -LiteralPath $ocrList -Force
                Write-Host '  Removed the OCR packs.'
            } catch {
                Write-Host '  Permission was declined; the OCR packs were kept.' -ForegroundColor Yellow
            }
            Remove-Item -LiteralPath $script -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
Write-Host "To finish, delete this folder: $app"
Write-Host 'If Install.bat installed Python 3.12 for you, you can remove it in Settings > Apps'
Write-Host '(only if nothing else on your PC uses it).'
