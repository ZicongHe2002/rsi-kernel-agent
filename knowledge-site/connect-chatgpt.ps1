$ErrorActionPreference = 'Stop'

$client = Join-Path $PSScriptRoot 'tools\tunnel-client\tunnel-client.exe'
$python = Join-Path $PSScriptRoot '.venv-mcp\Scripts\python.exe'
$server = Join-Path $PSScriptRoot 'mcp_server.py'
$profileDir = Join-Path $PSScriptRoot '.tunnel'
$savedId = Join-Path $profileDir 'tunnel-id.txt'
$previousKey = $env:CONTROL_PLANE_API_KEY
$secret = $null

try {
    if (-not (Test-Path -LiteralPath $client) -or -not (Test-Path -LiteralPath $python)) {
        throw 'Local setup is missing. See CHATGPT_SETUP.md in this folder.'
    }
    Write-Host 'Zhixu: connect your Markdown notes to ChatGPT (read and write).'
    & $python (Join-Path $PSScriptRoot 'launch.py') --no-browser
    if ($LASTEXITCODE -ne 0) { throw 'Could not start the knowledge website required for saving notes.' }
    Write-Host 'Create/select a tunnel: https://platform.openai.com/settings/organization/tunnels'
    $tunnelId = ''
    if (Test-Path -LiteralPath $savedId) {
        $tunnelId = (Get-Content -LiteralPath $savedId -Raw).Trim()
    }
    $enteredId = Read-Host "Tunnel ID (press Enter to reuse [$tunnelId])"
    if ($enteredId.Trim()) { $tunnelId = $enteredId.Trim() }
    if ($tunnelId -cnotmatch '^tunnel_([a-z0-9]{4}_)?[a-z0-9]{32}$') {
        throw 'Paste the complete tunnel ID from Platform settings.'
    }
    New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
    # Use forward slashes for the command parser; quote both paths for spaces.
    $mcpCommand = '"' + $python.Replace('\', '/') + '" "' + $server.Replace('\', '/') + '"'
    # Windows PowerShell 5 passes native arguments through CommandLineToArgvW.
    $nativeMcpCommand = $mcpCommand.Replace('"', '\"')
    & $client init --sample sample_mcp_stdio_local --profile zhixu-knowledge `
        --profile-dir $profileDir --tunnel-id $tunnelId --mcp-command $nativeMcpCommand `
        --control-plane-api-key-ref env:CONTROL_PLANE_API_KEY `
        --health-listen-addr 127.0.0.1:0 --force
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the local tunnel profile.' }
    Set-Content -LiteralPath $savedId -Value $tunnelId -Encoding ASCII

    $sharedLoader = Join-Path $env:USERPROFILE '.config\ai-secrets\Import-AiSecrets.ps1'
    if (-not $env:CONTROL_PLANE_API_KEY -and (Test-Path -LiteralPath $sharedLoader)) {
        & $sharedLoader -Names CONTROL_PLANE_API_KEY
    }
    if ($env:CONTROL_PLANE_API_KEY) {
        Write-Host 'Using the configured tunnel runtime key.'
    } else {
        Write-Host 'Runtime key: https://platform.openai.com/settings/organization/api-keys'
        Write-Host 'Enter the runtime API key here only. It is not saved to the profile.'
        $secret = Read-Host 'Runtime API key (hidden)' -AsSecureString
        if ($secret.Length -eq 0) { throw 'Runtime API key is required.' }
        $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        try {
            $env:CONTROL_PLANE_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
        }
    }
    & $client doctor --profile zhixu-knowledge --profile-dir $profileDir --explain
    if ($LASTEXITCODE -ne 0) { throw 'Tunnel checks failed. Read the error above before connecting in ChatGPT.' }

    Write-Host 'Starting tunnel. Keep this window open while using ChatGPT.'
    Write-Host 'Check readiness in the local status page, then add the tunnel in ChatGPT.'
    Write-Host 'Press Ctrl+C to stop.'
    & $client run --profile zhixu-knowledge --profile-dir $profileDir --open-web-ui
    if ($LASTEXITCODE -ne 0) { throw 'Tunnel stopped with an error. See the output above.' }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    $env:CONTROL_PLANE_API_KEY = $previousKey
    if ($null -ne $secret) { $secret.Dispose() }
}
