$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem

$zip = 'E:\flask_app_attendance_share.zip'
$z = [System.IO.Compression.ZipFile]::OpenRead($zip)

Write-Host "=== Total entries: $($z.Entries.Count) ===`n"

Write-Host "--- Check 1: junk files (should be empty) ---"
$junk = $z.Entries | Where-Object { $_.FullName -match '\.pyc$|/__pycache__/|/\.git/|/venv/|\.log$' }
if ($junk) { $junk.FullName | ForEach-Object { Write-Host "  BAD: $_" } }
else { Write-Host "  OK - no junk files`n" }

Write-Host "--- Check 2: config.py for real password ---"
$cfg = $z.Entries | Where-Object { $_.FullName -match 'config\.py$' -and $_.FullName -notmatch '__pycache__' }
foreach ($e in $cfg) {
    $reader = New-Object System.IO.StreamReader($e.Open())
    $content = $reader.ReadToEnd()
    $reader.Close()
    Write-Host "  File: $($e.FullName)"
    if ($content -match 'ltc19981118') {
        Write-Host "  !!! DANGER: real password 'ltc19981118' found in $($e.FullName)"
    }
    if ($content -match 'YOUR_MYSQL_PASSWORD') {
        Write-Host "  OK - placeholder 'YOUR_MYSQL_PASSWORD' present"
    }
}

Write-Host "`n--- Check 3: key files present ---"
$keyFiles = @('app.py', 'config.py', 'init_db.py', 'requirements.txt', 'README.md',
              'shape_predictor_68_face_landmarks.dat', 'templates/register.html', '.gitignore')
foreach ($f in $keyFiles) {
    $found = $z.Entries | Where-Object { $_.FullName -eq $f }
    if ($found) { Write-Host "  OK   $f ($([math]::Round($found.Length/1KB,1)) KB)" }
    else        { Write-Host "  MISS $f" }
}

Write-Host "`n--- Check 4: images_db face count ---"
$imgs = $z.Entries | Where-Object { $_.FullName -match '^images_db/.+\.jpg$' }
Write-Host "  $($imgs.Count) face images"

$z.Dispose()
Write-Host "`nDone."
