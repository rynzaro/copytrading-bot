from .conftest import add_user
from .test_loop import DAY, make


def rows(db, owner=None):
    q = "SELECT owner_id, created_at, payload FROM activity WHERE kind='still_active'"
    return db.execute(q).fetchall()


def test_weekly_push_created_once_per_week(db, server):
    add_user(db, "a.near", {"ntfy_topic": "t", "still_active_weekly": True})
    n, clock = make(db, server)
    n.weekly_still_active()
    n.weekly_still_active()
    assert len(rows(db)) == 1
    clock["now"] += 7 * DAY - 1
    n.weekly_still_active()
    assert len(rows(db)) == 1
    clock["now"] += 1
    n.weekly_still_active()
    assert len(rows(db)) == 2


def test_weekly_default_on_when_unset(db, server):
    add_user(db, "a.near", {"ntfy_topic": "t"})
    n, _ = make(db, server)
    n.weekly_still_active()
    assert len(rows(db)) == 1


def test_weekly_off_or_no_key_or_no_vault_or_no_channel(db, server):
    add_user(db, "off.near", {"ntfy_topic": "t", "still_active_weekly": False})
    add_user(db, "nokey.near", {"ntfy_topic": "t"}, bot_key=None)
    add_user(db, "novault.near", {"ntfy_topic": "t"}, vault=None)
    add_user(db, "nochan.near", {})
    n, _ = make(db, server)
    n.weekly_still_active()
    assert rows(db) == []


def test_weekly_key_check_hook(db, server):
    add_user(db, "a.near", {"ntfy_topic": "t"})
    add_user(db, "b.near", {"ntfy_topic": "t"}, vault="vb.near")
    n, _ = make(db, server)
    n.key_check = lambda vault_id, public_key: vault_id == "vb.near"
    n.weekly_still_active()
    assert [r[0] for r in rows(db)] == ["b.near"]


def test_weekly_delivered(db, server):
    add_user(db, "a.near", {"ntfy_topic": "t"})
    n, _ = make(db, server)
    n.run_once()
    assert [r["path"] for r in server.requests if r["path"] == "/t"] == ["/t"]
    assert b"still active" in next(r for r in server.requests if r["path"] == "/t")["body"]
