$ErrorActionPreference = 'Stop'
Set-Location E:\flask_app_attendance

$excludePattern = '\\__pycache__\\|\\\.git\\|\\venv\\|\\\.venv\\|\\static\\uploads\\|\\\.vscode\\|\\\.idea\\'

$files = Get-ChildItem -Recurse -File | Where-Object {
    $_.FullName -notmatch $excludePattern -and
    $_.Extension -notin '.pyc', '.log', '.tmp'
}

$totalMB = [math]::Round((($files | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host ("Files to package: {0}" -f $files.Count)
Write-Host ("Total size:       {0} MB" -f $totalMB)

$zip = 'E:\flask_app_attendance_share.zip'
if (Test-Path $zip) { Remove-Item $zip -Force }

Write-Host "Compressing... (this may take 1-3 minutes for ~115MB)"
Compress-Archive -Path $files.FullName -DestinationPath $zip -CompressionLevel Optimal

$zipSize = [math]::Round(((Get-Item $zip).Length / 1MB), 1)
Write-Host ("Done: {0} ({1} MB)" -f $zip, $zipSize)
