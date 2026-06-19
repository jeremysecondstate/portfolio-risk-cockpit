

| Header in your UI | Best source | Endpoint / method |
| ----- | ----- | ----- |
| Symbol | Databento symbology / FMP profile | FMP: `/stable/profile?symbol={symbol}` or Databento: `Historical.symbology.resolve(...)` |
| Company | FMP Profile; Databento Security Master | FMP: `/stable/profile?symbol={symbol}` or Databento: `Reference.security_master.get_range(...)` |
| Exchange | Both | FMP: `/stable/profile?symbol={symbol}` or Databento Security Master fields like `segment_mic` |
| Sector | FMP Profile | `/stable/profile?symbol={symbol}` |
| Industry | FMP Profile | `/stable/profile?symbol={symbol}` |
| Price | Databento live/OHLCV or FMP Quote | FMP: `/stable/quote?symbol={symbol}` or Databento: `Live.subscribe(...)` / `timeseries.get_range(schema="ohlcv-1d")` |
| Change % | FMP Quote or calculate from Databento | FMP: `/stable/quote?symbol={symbol}` or calculate from Databento current close vs prior close |
| Volume | Databento OHLCV/trades or FMP Quote | FMP: `/stable/quote?symbol={symbol}` or Databento: `timeseries.get_range(schema="ohlcv-1d")` |
| Avg vol | Databento historical OHLCV or FMP historical EOD | FMP: `/stable/historical-price-eod/full?symbol={symbol}` or Databento: `timeseries.get_range(schema="ohlcv-1d")`, then calculate rolling average |
| Market cap | FMP Profile / Market Cap; or calculate from Databento | FMP: `/stable/profile?symbol={symbol}` or `/stable/market-cap?symbol={symbol}`; Databento: close price × `shares_outstanding` |
| P/E | FMP Key Metrics | `/stable/key-metrics?symbol={symbol}` |
| EPS | FMP Income Statement / Earnings | `/stable/income-statement?symbol={symbol}` or `/stable/earnings?symbol={symbol}` |
| Rev growth | FMP Income Statement Growth / Financial Growth | `/stable/income-statement-growth?symbol={symbol}` or `/stable/financial-growth?symbol={symbol}` |
| Float/Shares | FMP Shares Float; Databento shares outstanding | FMP: `/stable/shares-float?symbol={symbol}`; Databento: `Reference.security_master.get_range(...)` |
| Next earnings | FMP Earnings Calendar / Earnings Report | `/stable/earnings-calendar` or `/stable/earnings?symbol={symbol}` |
| Recent filing | FMP SEC Filings by Symbol | `/stable/sec-filings-search/symbol?symbol={symbol}&from={date}&to={date}&page=0&limit=100` |
| Recent type | FMP SEC Filings by Symbol | Same as above; use the returned filing form/type field |
| Data | Your app / DB | No vendor endpoint — derive from whether your fetch job has data for that symbol |
| Complete | Your app / DB | No vendor endpoint — derive from your required-field completeness check |

