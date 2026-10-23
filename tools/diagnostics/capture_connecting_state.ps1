Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$captureRoot = $PSScriptRoot
$serialCaptureReceipt = [ordered]@{ started_at_utc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'); port = 'COM7'; exclusive_open_attempts = 0; opened = $false; closed = $false; dtr_requested = $false; rts_requested = $false; baud = 115200; data_bits = 8; parity = 'None'; stop_bits = 'One'; device_write_bytes = 0; received_characters = 0; raw_log_saved_or_output = $false; outcome = 'preparing' }
$serialReader = $null
$serialFilter = $null
$filterSafeOutTask = $null
$filterSafeErrorTask = $null
try {
    Add-Type -AssemblyName System.IO.Ports -ErrorAction Stop
    $filterInfo = [Diagnostics.ProcessStartInfo]::new()
    $filterInfo.FileName = (Get-Command python -ErrorAction Stop).Source
    $filterInfo.ArgumentList.Add('-B')
    $filterInfo.ArgumentList.Add((Join-Path $captureRoot 'sanitize_connecting_state.py'))
    $filterInfo.RedirectStandardInput = $true
    $filterInfo.RedirectStandardOutput = $true
    $filterInfo.RedirectStandardError = $true
    $filterInfo.UseShellExecute = $false
    $filterInfo.CreateNoWindow = $true
    $serialFilter = [Diagnostics.Process]::Start($filterInfo)
    $filterSafeOutTask = $serialFilter.StandardOutput.ReadToEndAsync()
    $filterSafeErrorTask = $serialFilter.StandardError.ReadToEndAsync()
    $serialReader = [IO.Ports.SerialPort]::new('COM7',115200,[IO.Ports.Parity]::None,8,[IO.Ports.StopBits]::One)
    $serialReader.Handshake = [IO.Ports.Handshake]::None
    $serialReader.DtrEnable = $false
    $serialReader.RtsEnable = $false
    $serialReader.ReadTimeout = 200
    $serialReader.WriteTimeout = 200
    $serialReader.Encoding = [Text.Encoding]::UTF8
    $serialCaptureReceipt.exclusive_open_attempts = 1
    $serialReader.Open()
    $serialCaptureReceipt.opened = $true
    $serialCaptureReceipt.outcome = 'capturing'
    Write-Output '{"stage":"serial_opened_exclusively","port":"COM7","dtr_requested":false,"rts_requested":false,"capture_seconds":45,"device_write_bytes":0}'
    $captureTimer = [Diagnostics.Stopwatch]::StartNew()
    while ($captureTimer.Elapsed.TotalSeconds -lt 45 -and $serialCaptureReceipt.received_characters -lt 262144) {
        $serialChunk = $serialReader.ReadExisting()
        if ($serialChunk.Length -gt 0) {
            $serialCaptureReceipt.received_characters += $serialChunk.Length
            $serialFilter.StandardInput.Write($serialChunk)
        }
        Start-Sleep -Milliseconds 100
    }
    $serialCaptureReceipt.capture_seconds = [Math]::Round($captureTimer.Elapsed.TotalSeconds,3)
    $serialCaptureReceipt.outcome = 'capture_complete'
} catch {
    $captureError = $_.Exception
    while ($captureError.InnerException) { $captureError = $captureError.InnerException }
    $serialCaptureReceipt.exception_type = $captureError.GetType().Name
    $serialCaptureReceipt.hresult_hex = ('0x{0:X8}' -f ($captureError.HResult -band 0xFFFFFFFFL))
    $serialCaptureReceipt.outcome = if (-not $serialCaptureReceipt.opened -and $serialCaptureReceipt.exclusive_open_attempts -eq 1) { 'exclusive_open_failed_no_takeover_or_retry' } else { 'capture_failed' }
} finally {
    if ($null -ne $serialReader) {
        try { if ($serialReader.IsOpen) { $serialReader.Close() }; $serialReader.Dispose(); $serialCaptureReceipt.closed = $true } catch { $serialCaptureReceipt.close_error_type = $_.Exception.GetType().Name }
    }
    if ($null -ne $serialFilter) {
        $serialFilter.StandardInput.Close()
        if (-not $serialFilter.WaitForExit(5000)) { $serialFilter.Kill(); $serialFilter.WaitForExit(); $serialCaptureReceipt.filter_outcome = 'own_filter_deadline' }
        else { $serialCaptureReceipt.filter_exit_code = $serialFilter.ExitCode }
        $safeFilterOutput = $filterSafeOutTask.GetAwaiter().GetResult()
        if ($safeFilterOutput.Length -le 65536) { Write-Output $safeFilterOutput }
        $filterErrorLength = $filterSafeErrorTask.GetAwaiter().GetResult().Length
        if ($filterErrorLength -gt 0) { $serialCaptureReceipt.filter_error_character_count = $filterErrorLength }
        $serialFilter.Dispose()
    }
    $serialCaptureReceipt.finished_at_utc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    $receiptPath = Join-Path $captureRoot 'serial-connecting-state-receipt.json'
    if (Test-Path -LiteralPath $receiptPath) { throw 'receipt_exists_check_outcome' }
    $receiptJson = $serialCaptureReceipt | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText($receiptPath,$receiptJson,[Text.UTF8Encoding]::new($false))
    Write-Output $receiptJson
}
