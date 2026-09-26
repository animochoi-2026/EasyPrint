param([string]$Version = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$versionSource = Get-Content -LiteralPath "$projectRoot/src/version.py" -Raw
$appVersion = [regex]::Match($versionSource, 'APP_VERSION = "([0-9.]+)"').Groups[1].Value
if (-not $Version) { $Version = $appVersion }
if ($Version -ne $appVersion) { throw 'Version must match src/version.py' }
$releaseDir = Join-Path $projectRoot "dist/release-$Version"
$workDir = Join-Path $projectRoot "build/release-$Version"
$zipPath = Join-Path $projectRoot "dist/EasyPrint-$Version-windows-x64.zip"
if (Test-Path -LiteralPath $zipPath) { throw 'Release ZIP already exists; choose a new version name.' }
& "$projectRoot/venv/Scripts/python.exe" -B "$PSScriptRoot/fetch_sumatra.py"
if ($LASTEXITCODE -ne 0) { throw 'Sumatra verification failed' }
& "$projectRoot/venv/Scripts/pyinstaller.exe" --onefile --windowed --name EasyPrintUpdater updater_main.py `
    --distpath "$workDir/helper" --workpath "$workDir/helper-work" --specpath "$workDir" --noconfirm
if ($LASTEXITCODE -ne 0) { throw 'Updater build failed' }
& "$projectRoot/venv/Scripts/pyinstaller.exe" --onedir --windowed --name EasyPrint main.py `
    --add-binary "$projectRoot/assets/SumatraPDF.exe;." `
    --add-binary "$workDir/helper/EasyPrintUpdater.exe;." `
    --add-data "$projectRoot/venv/Lib/site-packages/tkinterdnd2;tkinterdnd2" `
    --hidden-import win32print --hidden-import win32timezone --collect-all pymupdf `
    --distpath $releaseDir --workpath $workDir --specpath $workDir --noconfirm
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
Copy-Item -LiteralPath "$projectRoot/README_DIST.txt" -Destination "$releaseDir/EasyPrint/README.txt"
Copy-Item -LiteralPath "$projectRoot/LICENSE.txt" -Destination "$releaseDir/EasyPrint/LICENSE.txt"
Copy-Item -LiteralPath "$projectRoot/THIRD_PARTY_NOTICES.txt" -Destination "$releaseDir/EasyPrint/THIRD_PARTY_NOTICES.txt"
& "$projectRoot/venv/Scripts/python.exe" -B "$PSScriptRoot/collect_licenses.py" "$releaseDir/EasyPrint"
if ($LASTEXITCODE -ne 0) { throw 'License collection failed' }
& "$projectRoot/venv/Scripts/python.exe" -B "$PSScriptRoot/create_manifest.py" "$releaseDir/EasyPrint"
if ($LASTEXITCODE -ne 0) { throw 'Manifest creation failed' }
Compress-Archive -LiteralPath "$releaseDir/EasyPrint" -DestinationPath $zipPath
& "$projectRoot/venv/Scripts/python.exe" -B "$PSScriptRoot/verify_release.py" $zipPath
if ($LASTEXITCODE -ne 0) { throw 'ZIP verification failed' }
$checksum = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText("$zipPath.sha256", "$checksum  $(Split-Path -Leaf $zipPath)`n", [System.Text.Encoding]::ASCII)
Get-Item -LiteralPath $zipPath | Select-Object FullName,Length
