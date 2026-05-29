"""Pure name -> Symbol resolver against a Ticker Master DataFrame.

No I/O. Master is supplied by the caller (typically ``master.load()``).
"""

from __future__ import annotations

import pandas as pd

from trader.domain.types import Symbol


class UnknownTickerError(ValueError):
    def __init__(self, query: str) -> None:
        super().__init__(f"no ticker matches {query!r}")
        self.query = query


class AmbiguousTickerError(ValueError):
    def __init__(self, query: str, candidates: list[str]) -> None:
        super().__init__(f"{query!r} matches multiple symbols: {candidates}")
        self.query = query
        self.candidates = candidates


def resolve(master: pd.DataFrame, query: str) -> Symbol:
    q = query.strip()
    if not q:
        raise UnknownTickerError(query)
    if len(q) == 6 and q.isdigit():
        return Symbol(q)
    qf = q.casefold()
    matches: set[str] = set()
    for col in ("name_ko", "name_en"):
        col_normalised = master[col].fillna("").astype(str).str.strip().str.casefold()
        hits = master.loc[col_normalised == qf, "symbol"].astype(str).tolist()
        matches.update(hits)
    if not matches:
        raise UnknownTickerError(query)
    if len(matches) > 1:
        raise AmbiguousTickerError(query, sorted(matches))
    return Symbol(next(iter(matches)))
