# Scheduled History Backfill

The local **History Store** (`data/history_{KIS_ENV}.sqlite`) is backfilled by
the Streamlit app on open, but the app is not always open at KRX close. To
guarantee daily capture, register a Windows Task Scheduler job that runs the
sync after KRX close (~16:00 KST).

The CLI entry point is `python -m trader.history` (which runs
`trader/history/__main__.py`). Do **not** call `python -m trader.history.sync`
— that imports the module without executing anything and exits silently.

## One-time setup (recommended via bundled batch wrapper)

`scripts/sync_history.bat` handles working directory, env vars, unbuffered
stdout, and appending to `data/sync.log`. Register one task per env so both
the Mock and Real history DBs stay current:

```powershell
$bat = "C:\path\to\autotrader\scripts\sync_history.bat"
schtasks /Create /SC DAILY /ST 16:00 /TN "AutotraderHistorySync-Mock" `
  /TR "$bat mock" /F
schtasks /Create /SC DAILY /ST 16:05 /TN "AutotraderHistorySync-Real" `
  /TR "$bat real" /F
```

The wrapper accepts the env name as its single argument (`mock` or `real`),
defaulting to `mock` if omitted. Five-minute stagger between tasks avoids
sharing token-issuance rate-limit windows.

## Verifying

```powershell
schtasks /Query /TN "AutotraderHistorySync-Mock"
schtasks /Run   /TN "AutotraderHistorySync-Mock"
Get-Content C:\path\to\autotrader\data\sync.log -Tail 10
Get-ScheduledTaskInfo -TaskName "AutotraderHistorySync-Mock" | Format-List LastRunTime, LastTaskResult
```

A successful run appends a line like
`pulled=N inserted=M already=K window=YYYY-MM-DD..YYYY-MM-DD db=data\history_mock.sqlite`
to `data/sync.log`. The job is idempotent — re-running mid-day produces
`inserted=0` on a steady-state Store.

## Notes

- The wrapper sets `PYTHONUNBUFFERED=1` so prints flush through the redirect.
  Without it, Windows buffers stdout when not attached to a console and the
  log appears empty even though the task succeeds.
- The CLI passes `EXCG_ID_DVSN_CD=KRX` by default. NXT/SOR routing is a
  per-Streamlit-session UI choice, not relevant to backfill (read-only).
- Detection of "did the scheduler actually run today" is intentionally out of
  scope. The on-app-open backfill in `ui/_common.bootstrap_backfill()` is the
  safety net.
