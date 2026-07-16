# Registers the daily 8:00am BidMatch autorun task. Run once as Administrator.
#
# Scheduled tasks do NOT inherit the registering shell's environment, so
# setting $env:PYTHONPATH here would have no effect on the task at run time.
# Instead the action's argument is a small inline wrapper that pushes "src"
# onto sys.path itself before importing bidmatch, so no PYTHONPATH is needed.
#
# Reference only (requires PYTHONPATH=src to already be set in the task's
# environment — not used because Task Scheduler won't carry it):
#   -Argument "-m bidmatch.autorun.daily"

$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$wrapper = "import sys; sys.path.insert(0, 'src'); from bidmatch.autorun.daily import main; sys.exit(main())"
$action = New-ScheduledTaskAction -Execute $python -Argument "-c `"$wrapper`"" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Daily -At 8:00am
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName "BidMatch Daily Autorun" -Action $action -Trigger $trigger -Settings $settings -Description "CPAgent1 BidMatch daily pipeline (8:00am)" -Force
Write-Host "Registered 'BidMatch Daily Autorun' (daily 8:00am, wake-to-run, run-if-missed)."
Write-Host "Verify with: Start-ScheduledTask -TaskName 'BidMatch Daily Autorun'; then check output/autorun.log"
