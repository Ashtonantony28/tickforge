# Data sources

Nothing here is committed to git; run `python data/fetch.py <source>` on a machine with network access.
SHA-256 values in `data/checksums.json` are pinned on first download (`--pin`) and then reviewed.

| Source id | What | Notes |
|---|---|---|
| `itch-sample` | Nasdaq TotalView-ITCH 5.0 full-day sample file (gzip) | Public sample-data site; several GB. Terms: sample/research use. |
| `binance-trades` | Binance public data dump, daily trades for a symbol | data.binance.vision; used from M3. |
| `lobster-sample` | LOBSTER free sample (reconstructed L1-L50 + messages) | Manual download (form); place under `data/raw/lobster/`. |

Verified 2026-11-12 from the cloud container: the Nasdaq sample host lists 15 days (2018-2020);
`data.binance.vision` dumps download and match Binance's published `.CHECKSUM`.
`api.binance.com` / `stream.binance.com` return HTTP 451 from the cloud region, so live recording
uses the market-data-only hosts `data-api.binance.vision` and `data-stream.binance.vision`
(`python -m tickforge.recorder`, ADR 0008).
