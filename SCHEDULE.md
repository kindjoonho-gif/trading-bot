# Scheduled History Backfill

The local **History Store** (`data/history_{KIS_ENV}.sqlite`) is backfilled by
the Streamlit app on open, but the app is not always open at KRX close. To
guarantee daily capture, register a Windows Task Scheduler job that runs
`python -m trader.history.sync` after KRX close (~16:00 KST).

## One-time setup

From an elevated PowerShell, replace the python path with the project's `.venv`
copy and run:

```powershell
schtasks /Create /SC DAILY /ST 16:00 /TN "AutotraderHistorySync" `
  /TR "C:\path\to\autotrader\.venv\Scripts\python.exe -m trader.history.sync" /F
```

Or via the GUI (Task Scheduler → Create Basic Task):
- **Trigger**: Daily, 16:00 KST, weekdays only
- **Action**: Start a program — `C:\path\to\autotrader\.venv\Scripts\python.exe`
- **Arguments**: `-m trader.history.sync`
- **Start in**: `C:\path\to\autotrader`

## Logging

The CLI writes a one-line summary to stdout. Redirect inside the scheduled task
command to capture history:

```powershell
schtasks /Create /SC DAILY /ST 16:00 /TN "AutotraderHistorySync" `
  /TR "cmd /c C:\path\to\autotrader\.venv\Scripts\python.exe -m trader.history.sync >> C:\path\to\autotrader\data\sync.log 2>&1" /F
```

## Verifying

```powershell
schtasks /Query /TN "AutotraderHistorySync"
schtasks /Run /TN "AutotraderHistorySync"
```

Then `Get-Content data\history_mock.sqlite` should be larger than before and
`Get-Content data\sync.log -Tail 5` should show the latest summary.

## Notes

- The CLI uses the active `KIS_ENV` from `.env`. To backfill the real-account
  Store as well, schedule a second task that prepends `set KIS_ENV=real`.
- The job is idempotent — re-running mid-day is safe and produces
  `inserted=0` on a steady-state Store.
- Detection of "did the scheduler actually run today" is intentionally out of
  scope. The on-app-open backfill is the safety net.
