# Autotrader

Portfolio rebalancing and manual order placement against a brokerage account.

**Phase A:** KOSPI via Korea Investment Securities (KIS). Manual buy/sell (market or limit) + basket rebalance by target weights. Streamlit UI.

**Phase B (later):** US equities (Alpaca/IBKR), crypto (Binance/Upbit), stop-loss, WebSocket tape, algo strategies, persistent history DB.

Domain language: see [CONTEXT.md](./CONTEXT.md).

## Setup

```powershell
# Install uv (https://docs.astral.sh/uv/) if not present
winget install astral-sh.uv

# Sync deps
uv sync

# Copy and fill in KIS credentials
Copy-Item .env.example .env
# edit .env with KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO
```

## Run

```powershell
# Streamlit UI
uv run streamlit run ui/streamlit_app.py

# Tests
uv run pytest
uv run mypy
uv run ruff check
```

## Safety

- `KIS_ENV` selects mock (`모의투자`) or real account at startup.
- The UI **LIVE Mode** toggle is independent and defaults off — even on a Live Account, orders go through Dry-run until you flip it.
- Rebalance always shows a Plan before submitting.
