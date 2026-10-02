# F1 Telemetry Dashboard (f1dash) installer for Windows (DIST-04).
#
#   irm https://raw.githubusercontent.com/mricero/F1-Telemetry-Dashboard/main/install.ps1 | iex
#
# What it does, without admin rights and without touching your own Python:
#   1. installs uv (Astral's official installer) if `uv` is missing;
#   2. installs the latest release of f1dash as a uv tool (uv fetches Python 3.12 itself);
#   3. puts uv's tool folder on PATH (`uv tool update-shell`);
#   4. adds a Start-menu shortcut "F1 Replay".
# Running it again updates to the newest release.
#
# F1DASH_SOURCE=<path or git URL> installs from there instead (CI uses the checkout);
# F1DASH_NO_SHORTCUT=1 skips the shortcut.

& {
    $ErrorActionPreference = 'Stop'
    $Repo = 'mricero/F1-Telemetry-Dashboard'

    function Invoke-Native {
        param([string]$File, [string[]]$Arguments)
        & $File @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$File $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
        }
    }

    # 1. uv
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host 'Installing uv (https://docs.astral.sh/uv/) ...'
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        foreach ($candidate in @("$env:USERPROFILE\.local\bin", "$env:USERPROFILE\.cargo\bin")) {
            if (Test-Path (Join-Path $candidate 'uv.exe')) { $env:Path = "$candidate;$env:Path" }
        }
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            throw 'uv was installed but is not on PATH yet. Open a new terminal and run this again.'
        }
    }

    # 2. f1dash, from the latest release tag (or main when there is none yet)
    $Source = $env:F1DASH_SOURCE
    if (-not $Source) {
        $Ref = 'main'
        try {
            $Release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -TimeoutSec 15
            if ($Release.tag_name) { $Ref = $Release.tag_name }
        } catch {
            Write-Host 'No release found on GitHub; installing from main.'
        }
        $Source = "git+https://github.com/$Repo@$Ref"
    }
    Write-Host "Installing f1dash from $Source ..."
    Invoke-Native uv @('tool', 'install', '--python', '3.12', '--reinstall', $Source)

    # 3. PATH
    Invoke-Native uv @('tool', 'update-shell')

    # 4. Start-menu shortcut
    if (-not $env:F1DASH_NO_SHORTCUT) {
        $BinDir = (& uv tool dir --bin).Trim()
        $Target = Join-Path $BinDir 'f1dash.exe'
        $Programs = [Environment]::GetFolderPath('Programs')
        $Shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $Programs 'F1 Replay.lnk'))
        $Shortcut.TargetPath = $Target
        $Shortcut.WorkingDirectory = $env:USERPROFILE
        $Shortcut.Description = 'F1 Telemetry Dashboard'
        $Shortcut.Save()
        Write-Host 'Added the Start-menu shortcut "F1 Replay".'
    }

    Write-Host ''
    Write-Host 'Done. Run: f1dash   (open a new terminal first if the command is not found)'
    Write-Host 'Live car telemetry and positions need your own F1TV subscription token:'
    Write-Host 'set F1TV_SUBSCRIPTION_TOKEN in the .env file that `f1dash paths` shows.'
}
