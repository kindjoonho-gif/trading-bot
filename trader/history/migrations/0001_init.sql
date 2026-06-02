CREATE TABLE IF NOT EXISTS trades (
    odno      TEXT NOT NULL,
    ord_date  TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    side      TEXT NOT NULL,
    quantity  TEXT NOT NULL,
    avg_price TEXT NOT NULL,
    ord_time  TEXT NOT NULL,
    PRIMARY KEY (odno, ord_date)
);

CREATE INDEX IF NOT EXISTS idx_trades_ord_date ON trades (ord_date);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_ord_date ON trades (symbol, ord_date);
