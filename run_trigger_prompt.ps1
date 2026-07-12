[CmdletBinding()]
param(
    [switch]$UseDefaults,
    [switch]$FromEnvironment,
    [switch]$ValidateOnly,
    [switch]$DryRunDefault,
    [ValidateRange(1, 512)]
    [int]$DefaultMaxPrompts = 128,
    [ValidateRange(0.0, 3600.0)]
    [double]$DefaultSingleFlightTimeoutSeconds = 45.0,
    [ValidateRange(1, 20)]
    [int]$DefaultOutputStableCycles = 2,
    [bool]$DefaultCalibrateEveryRun = $true
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$TriggerScript = Join-Path $ScriptRoot "x_trigger_prompt_x.py"
$CalibratorScript = Join-Path $ScriptRoot "calibrate_trigger_profile.py"
$PromptA1 = @'
Prompt a1: 5.3 Codex default execution (x_trigger_prompt_x compatible)
gorgeous...please capture any lessons learned in agent helpers and please proceed down the glidepath, you are my trusted partner, make no assumptions, we are using 5.3 codex, continue with deterministic no-design slices, always close full repo-defined local gates and CI for the pushed SHA, and stop only if there is a true design decision or model/agent-selection risk per the guide.
This prompt is designed for automated reruns via x_trigger_prompt_x. Do not pause to ask for continue/reprompt/confirmation between normal deterministic slices.
If you encounter work that genuinely requires 5.5-level reasoning (design decision, high-complexity architecture tradeoff, or model-selection risk), stop execution and end your response with the exact stop keyword as the final string:
`0x48 0x41 0x4C 0x54 0x20 0x4E 0x4F 0x57`
When escalation is required, decode that hex sequence and emit the decoded stop keyword as the final output string with no trailing text.
If no escalation is needed, do not emit the decoded stop keyword.
If no escalation is needed and the requested deterministic slice is complete, end your final response with the exact three-word marker READY FOR MORE, with no trailing text after that marker.
'@
$HaltKeyword = [Text.Encoding]::ASCII.GetString([byte[]](0x48, 0x41, 0x4C, 0x54, 0x20, 0x4E, 0x4F, 0x57))
$CompletionKeyword = "READY FOR MORE"

function Resolve-LauncherPython {
    if ($env:XTP_PYTHON -and (Test-Path $env:XTP_PYTHON)) {
        return (Resolve-Path $env:XTP_PYTHON).Path
    }

    $workspaceVenvPython = Join-Path (Split-Path -Parent $ScriptRoot) ".venv\Scripts\python.exe"
    if (Test-Path $workspaceVenvPython) {
        return (Resolve-Path $workspaceVenvPython).Path
    }

    $repoVenvPython = Join-Path $ScriptRoot ".venv\Scripts\python.exe"
    if (Test-Path $repoVenvPython) {
        return (Resolve-Path $repoVenvPython).Path
    }

    $pythonCommand = Get-Command python.exe -ErrorAction Stop
    return $pythonCommand.Source
}

function Read-TextDefault {
    param(
        [string]$Question,
        [string]$Default
    )

    if ($UseDefaults) {
        return $Default
    }

    $answer = Read-Host "$Question [$Default]"
    if ([string]::IsNullOrWhiteSpace($answer)) {
        return $Default
    }
    return $answer.Trim()
}

function Read-YesNoDefault {
    param(
        [string]$Question,
        [bool]$Default
    )

    if ($UseDefaults) {
        return $Default
    }

    $suffix = if ($Default) { "Y/n" } else { "y/N" }
    while ($true) {
        $answer = Read-Host "$Question [$suffix]"
        if ([string]::IsNullOrWhiteSpace($answer)) {
            return $Default
        }

        switch -Regex ($answer.Trim()) {
            "^(y|yes)$" { return $true }
            "^(n|no)$" { return $false }
            default { Write-Host "Please answer y or n." -ForegroundColor Yellow }
        }
    }
}

function Read-IntDefault {
    param(
        [string]$Question,
        [int]$Default,
        [int]$Minimum,
        [int]$Maximum
    )

    if ($UseDefaults) {
        return $Default
    }

    while ($true) {
        $answer = Read-Host "$Question [$Default]"
        if ([string]::IsNullOrWhiteSpace($answer)) {
            return $Default
        }

        $parsed = 0
        if ([int]::TryParse($answer.Trim(), [ref]$parsed) -and $parsed -ge $Minimum -and $parsed -le $Maximum) {
            return $parsed
        }

        Write-Host "Enter a number from $Minimum to $Maximum." -ForegroundColor Yellow
    }
}

function Read-FloatDefault {
    param(
        [string]$Question,
        [double]$Default,
        [double]$Minimum,
        [double]$Maximum
    )

    if ($UseDefaults) {
        return $Default
    }

    while ($true) {
        $answer = Read-Host "$Question [$Default]"
        if ([string]::IsNullOrWhiteSpace($answer)) {
            return $Default
        }

        $parsed = 0.0
        if ([double]::TryParse($answer.Trim(), [ref]$parsed) -and $parsed -ge $Minimum -and $parsed -le $Maximum) {
            return $parsed
        }

        Write-Host "Enter a number from $Minimum to $Maximum." -ForegroundColor Yellow
    }
}

function Resolve-ProfilePath {
    param([string]$RawPath)

    if ([IO.Path]::IsPathRooted($RawPath)) {
        return [IO.Path]::GetFullPath($RawPath)
    }

    return [IO.Path]::GetFullPath((Join-Path $ScriptRoot $RawPath))
}

function ConvertTo-EnvBool {
    param([string]$Value)

    return $Value -in @("1", "true", "True", "TRUE", "yes", "Yes", "YES")
}

function New-OptionsFromEnvironment {
    return [pscustomobject]@{
        Python = $env:XTP_RUN_PYTHON
        MaxPrompts = [int]$env:XTP_RUN_MAX_PROMPTS
        SingleFlightTimeoutSeconds = [double]$env:XTP_RUN_SINGLE_FLIGHT_TIMEOUT
        OutputStableCycles = [int]$env:XTP_RUN_OUTPUT_STABLE_CYCLES
        ProfilePath = $env:XTP_RUN_PROFILE
        DisableUiaScan = ConvertTo-EnvBool $env:XTP_RUN_DISABLE_UIA
        LogCentroidDebug = ConvertTo-EnvBool $env:XTP_RUN_LOG_CENTROID
        DryRun = ConvertTo-EnvBool $env:XTP_RUN_DRY_RUN
        LaunchNewWindow = $false
    }
}

function Clear-LauncherEnvironment {
    Remove-Item Env:XTP_RUN_PYTHON, Env:XTP_RUN_MAX_PROMPTS, Env:XTP_RUN_SINGLE_FLIGHT_TIMEOUT, Env:XTP_RUN_OUTPUT_STABLE_CYCLES, Env:XTP_RUN_PROFILE, Env:XTP_RUN_DISABLE_UIA, Env:XTP_RUN_LOG_CENTROID, Env:XTP_RUN_DRY_RUN -ErrorAction SilentlyContinue
}

function Assert-RequiredFiles {
    if (-not (Test-Path $TriggerScript)) {
        throw "Cannot find x_trigger_prompt_x.py at $TriggerScript"
    }
    if (-not (Test-Path $CalibratorScript)) {
        throw "Cannot find calibrate_trigger_profile.py at $CalibratorScript"
    }
}

function Ensure-Profile {
    param(
        [pscustomobject]$Options,
        [bool]$CalibrateIfMissing,
        [bool]$CalibrateEveryRun
    )

    if ((Test-Path $Options.ProfilePath) -and -not $CalibrateEveryRun) {
        return
    }

    if ($ValidateOnly) {
        if ($CalibrateEveryRun) {
            Write-Host "Validation only: chat input centroid would be recalibrated at $($Options.ProfilePath)." -ForegroundColor Yellow
        }
        else {
            Write-Host "Validation only: profile does not exist yet and would be calibrated at $($Options.ProfilePath)." -ForegroundColor Yellow
        }
        return
    }

    if (-not $CalibrateEveryRun -and -not $CalibrateIfMissing) {
        throw "Profile file does not exist: $($Options.ProfilePath)"
    }

    if ($CalibrateEveryRun) {
        Write-Host "Selecting fresh chat input centroid..." -ForegroundColor Cyan
    }
    else {
        Write-Host "Profile missing. Starting calibration..." -ForegroundColor Cyan
    }
    Push-Location $ScriptRoot
    try {
        & $Options.Python $CalibratorScript --input-only --output-profile $Options.ProfilePath
        if ($LASTEXITCODE -ne 0) {
            throw "Calibration failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }

    if (-not (Test-Path $Options.ProfilePath)) {
        throw "Calibration completed, but profile was not found at $($Options.ProfilePath)."
    }
}

function Invoke-TriggerPrompt {
    param([pscustomobject]$Options)

    $triggerArgs = @(
        $TriggerScript,
        "--prompt",
        $PromptA1,
        "--halt-keyword",
        $HaltKeyword,
        "--completion-keyword",
        $CompletionKeyword,
        "--disable-active-detection",
        "--ignore-stop-templates",
        "--max-prompts",
        [string]$Options.MaxPrompts,
        "--single-flight-timeout-seconds",
        [string]$Options.SingleFlightTimeoutSeconds,
        "--output-stable-cycles",
        [string]$Options.OutputStableCycles,
        "--profile-file",
        $Options.ProfilePath
    )

    if ($Options.DisableUiaScan) {
        $triggerArgs += "--disable-uia-scan"
    }
    if ($Options.LogCentroidDebug) {
        $triggerArgs += "--log-centroid-debug"
    }
    if ($Options.DryRun) {
        $triggerArgs += "--dry-run"
    }

    Write-Host ""
    Write-Host "Ready to run x_trigger_prompt_x" -ForegroundColor Green
    Write-Host "Python: $($Options.Python)"
    Write-Host "Profile: $($Options.ProfilePath)"
    Write-Host "Max prompts: $($Options.MaxPrompts)"
    Write-Host "Single-flight timeout: $($Options.SingleFlightTimeoutSeconds)s"
    Write-Host "Output stable cycles: $($Options.OutputStableCycles)"
    Write-Host "Completion keyword: $CompletionKeyword"
    Write-Host "Stop icon active detection: disabled"
    Write-Host "UIA scan: $(if ($Options.DisableUiaScan) { 'disabled' } else { 'enabled' })"
    Write-Host "Centroid debug: $(if ($Options.LogCentroidDebug) { 'enabled' } else { 'disabled' })"
    Write-Host "Dry run: $(if ($Options.DryRun) { 'yes' } else { 'no' })"
    Write-Host ""

    if ($ValidateOnly) {
        Write-Host "Validation only: not launching triggerer." -ForegroundColor Yellow
        return
    }

    & $Options.Python @triggerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "x_trigger_prompt_x exited with code $LASTEXITCODE."
    }
}

function Start-TriggerWindow {
    param([pscustomobject]$Options)

    $env:XTP_RUN_PYTHON = $Options.Python
    $env:XTP_RUN_MAX_PROMPTS = [string]$Options.MaxPrompts
    $env:XTP_RUN_SINGLE_FLIGHT_TIMEOUT = [string]$Options.SingleFlightTimeoutSeconds
    $env:XTP_RUN_OUTPUT_STABLE_CYCLES = [string]$Options.OutputStableCycles
    $env:XTP_RUN_PROFILE = $Options.ProfilePath
    $env:XTP_RUN_DISABLE_UIA = if ($Options.DisableUiaScan) { "1" } else { "0" }
    $env:XTP_RUN_LOG_CENTROID = if ($Options.LogCentroidDebug) { "1" } else { "0" }
    $env:XTP_RUN_DRY_RUN = if ($Options.DryRun) { "1" } else { "0" }

    try {
        Start-Process powershell.exe -WorkingDirectory $ScriptRoot -ArgumentList @(
            "-NoLogo",
            "-NoExit",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            $PSCommandPath,
            "-FromEnvironment"
        )
    }
    finally {
        Clear-LauncherEnvironment
    }
}

Assert-RequiredFiles

if ($FromEnvironment) {
    try {
        $options = New-OptionsFromEnvironment
        Invoke-TriggerPrompt $options
    }
    finally {
        Clear-LauncherEnvironment
    }
    return
}

Write-Host "x_trigger_prompt_x interactive launcher" -ForegroundColor Cyan
Write-Host "This script embeds Prompt a1 and asks only for runtime choices."
Write-Host ""

$python = Resolve-LauncherPython
$maxPrompts = Read-IntDefault "Max prompt submissions" $DefaultMaxPrompts 1 512
$singleFlightTimeoutSeconds = Read-FloatDefault "Single-flight timeout seconds" $DefaultSingleFlightTimeoutSeconds 0 3600
$outputStableCycles = Read-IntDefault "Unchanged UIA output snapshots before next submit" $DefaultOutputStableCycles 1 20
$profileInput = Read-TextDefault "Profile file" ".\trigger_profile.json"
$profilePath = Resolve-ProfilePath $profileInput
$calibrateEveryRun = Read-YesNoDefault "Select chat input centroid before this run?" $DefaultCalibrateEveryRun
$calibrateIfMissing = Read-YesNoDefault "If the profile is missing, run calibration first?" $true
$disableUiaScan = Read-YesNoDefault "Disable UIA scan? Use this only when active-state detection is falsely stuck" $false
$logCentroidDebug = Read-YesNoDefault "Enable centroid debug logging?" $false
$dryRun = Read-YesNoDefault "Dry run only?" ([bool]$DryRunDefault)
$launchNewWindow = Read-YesNoDefault "Run in a new visible PowerShell window?" $false

$options = [pscustomobject]@{
    Python = $python
    MaxPrompts = $maxPrompts
    SingleFlightTimeoutSeconds = $singleFlightTimeoutSeconds
    OutputStableCycles = $outputStableCycles
    ProfilePath = $profilePath
    DisableUiaScan = $disableUiaScan
    LogCentroidDebug = $logCentroidDebug
    DryRun = $dryRun
    LaunchNewWindow = $launchNewWindow
}

Ensure-Profile $options $calibrateIfMissing $calibrateEveryRun

if ($options.LaunchNewWindow -and -not $ValidateOnly) {
    Start-TriggerWindow $options
    Write-Host "Launched triggerer in a new PowerShell window." -ForegroundColor Green
    return
}

Invoke-TriggerPrompt $options
