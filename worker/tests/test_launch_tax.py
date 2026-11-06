from copytrade_worker import db, launch_tax

MIN = 60_000


def row(tax=True, checked=0):
    return {"token_id": "t", "code_hash": "h", "sniping_tax": tax, "checked_at": checked}


def test_hit_rules():
    now = 100 * MIN
    assert launch_tax.hit(row(), created_ms=now - 5 * MIN, now=now)
    assert launch_tax.hit(row(), created_ms=now - 10 * MIN, now=now)
    assert not launch_tax.hit(row(), created_ms=now - 10 * MIN - 1, now=now)  # older than 10 min: no check
    assert not launch_tax.hit(None, created_ms=now, now=now)  # miss: no check
    assert not launch_tax.hit(row(tax=False), created_ms=now, now=now)
    assert launch_tax.hit(row(checked=now - MIN), created_ms=None, now=now)  # age unknown: from checked_at
    assert not launch_tax.hit(row(checked=now - 11 * MIN), created_ms=None, now=now)


class FakeChain:
    def __init__(self, launches, code):
        self._l, self.code, self.checked = launches, code, []

    def launches(self, parents, since_ms):
        assert "v2-launchpad.justhoot.near" in parents and "nearlytrade.near" in parents
        return [{"account_id": t} for t in self._l]

    def code_check(self, t):
        self.checked.append(t)
        return self.code.get(t)


def test_watcher_caches_once_and_retries_errors():
    conn = db.connect(":memory:")
    ch = FakeChain(
        ["a.nearlytrade.near", "b.v2-launchpad.justhoot.near", "c.nearlytrade.near"],
        {
            "a.nearlytrade.near": {"sniping_tax": True, "code_hash": "H"},
            "b.v2-launchpad.justhoot.near": {"sniping_tax": False, "code_hash": "G"},
        },
    )
    w = launch_tax.Watcher(conn, ch)
    assert w.cycle(now=1000) == 2
    assert db.get_launch_tax(conn, "a.nearlytrade.near") == {
        "token_id": "a.nearlytrade.near",
        "code_hash": "H",
        "sniping_tax": True,
        "checked_at": 1000,
    }
    assert db.get_launch_tax(conn, "b.v2-launchpad.justhoot.near")["sniping_tax"] is False
    assert db.get_launch_tax(conn, "c.nearlytrade.near") is None
    ch.checked.clear()
    for _ in range(5):
        w.cycle(now=2000)
    assert ch.checked == ["c.nearlytrade.near"] * (launch_tax.MAX_TRIES - 1)  # cached ones never re-read; errors capped
