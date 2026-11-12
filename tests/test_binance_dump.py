import io
import zipfile

import pytest

from tickforge.binance_dump import load_trades, parse_trades_csv, summary


def test_parse_us_and_ms_timestamps(tmp_path):
    csv = ("1,75368.41000000,0.00013000,9.79,1794268800075986,True,True\n"
           "2,75368.42000000,0.5,1.0,1704067200000,False,True\n")
    a = parse_trades_csv(io.StringIO(csv))
    assert a["t_ns"].tolist() == [1794268800075986000, 1704067200000000000]
    assert a["price"].tolist() == [7536841000000, 7536842000000]
    assert a["qty"].tolist() == [13000, 50000000]
    assert a["buyer_maker"].tolist() == [True, False]
    p = tmp_path / "X-trades.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("X-trades.csv", csv)
    s = summary(load_trades(p))
    assert s["trades"] == 2 and s["missing_ids"] == 0


def test_rejects_unordered_ids():
    with pytest.raises(ValueError):
        parse_trades_csv(io.StringIO("2,1,1,1,1704067200000,True,True\n1,1,1,1,1704067200000,True,True\n"))
