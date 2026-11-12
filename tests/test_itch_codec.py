from tickforge import itch


def test_encoded_lengths_match_spec():
    assert len(itch.add(1, 2, 3, "B", 100, "AAPL", 10000)) == 2 + 36
    assert len(itch.add(1, 2, 3, "B", 100, "AAPL", 10000, mpid="GSCO")) == 2 + 40
    assert len(itch.executed(1, 2, 3, 100, 9)) == 2 + 31
    assert len(itch.executed_price(1, 2, 3, 100, 9, 10000)) == 2 + 36
    assert len(itch.cancel(1, 2, 3, 100)) == 2 + 19 + 4
    assert len(itch.delete(1, 2, 3)) == 2 + 19
    assert len(itch.replace(1, 2, 3, 4, 100, 10000)) == 2 + 35
    assert len(itch.trade(1, 2, 3, "S", 100, "AAPL", 10000, 9)) == 2 + 44
    assert len(itch.stock_directory(1, 2, "AAPL")) == 2 + 39
    assert len(itch.system_event(1, "O")) == 2 + 12


def test_roundtrip_decode():
    buf = itch.add(123456789, 7, 42, "S", 300, "MSFT", 4_100_500) + itch.replace(5, 7, 42, 43, 200, 4_100_600)
    msgs = list(itch.decode(buf))
    assert msgs[0].type == "A" and msgs[0].locate == 7 and msgs[0].ts == 123456789
    assert msgs[0].fields == (42, "S", 300, 4_100_500)
    assert msgs[1].type == "U" and msgs[1].fields == (42, 43, 200, 4_100_600)
