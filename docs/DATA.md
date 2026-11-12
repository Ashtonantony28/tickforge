# Data sources

Nothing here is committed to git; run `python data/fetch.py <source>` on a machine with network access.
SHA-256 values in `data/checksums.json` are pinned on first download (`--pin`) and then reviewed.

| Source id | What | Notes |
|---|---|---|
| `itch-sample` | Nasdaq TotalView-ITCH 5.0 full-day sample file (gzip) | Public sample-data site; several GB. Terms: sample/research use. |
| `binance-trades` | Binance public data dump, daily trades for a symbol | data.binance.vision; used from M3. |
| `lobster-sample` | LOBSTER free sample (reconstructed L1-L50 + messages) | Manual download (form); place under `data/raw/lobster/`. |

Availability was not verifiable from the development container (outbound hosts blocked); URLs are
re-checked on first local use and corrected here if they moved.
