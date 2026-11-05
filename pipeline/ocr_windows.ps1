# OCRs every PNG in a folder with the Windows built-in OCR engine (Windows PowerShell 5.1).
# Usage: powershell -File ocr_windows.ps1 -InputDir <pngs> -OutputFile <txt>
param([string]$InputDir, [string]$OutputFile)

Add-Type -AssemblyName System.Runtime.WindowsRuntime
[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Storage.StorageFile, Windows.Foundation, ContentType = WindowsRuntime]

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Wait-Async($operation, [Type]$resultType) {
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.Wait(-1) | Out-Null
    $task.Result
}

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$pages = New-Object System.Collections.Generic.List[string]

foreach ($png in Get-ChildItem -Path $InputDir -Filter *.png | Sort-Object Name) {
    $file = Wait-Async ([Windows.Storage.StorageFile]::GetFileFromPathAsync($png.FullName)) ([Windows.Storage.StorageFile])
    $stream = Wait-Async ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Wait-Async ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Wait-Async ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Wait-Async ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = $result.Lines | ForEach-Object { $_.Text }
    $pages.Add("=== PAGE $($png.BaseName) ===`n" + ($lines -join "`n"))
    $stream.Dispose()
}

[System.IO.File]::WriteAllText($OutputFile, ($pages -join "`n"), [System.Text.Encoding]::UTF8)
"OCR complete: $($pages.Count) pages"
