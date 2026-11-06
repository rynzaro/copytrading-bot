import json

from copytrade_worker.notify.linking import parse_link_command

from .conftest import add_user
from .test_loop import DAY, TOKEN, make, tg_reqs


def update(uid, text, chat_id=-100555, kind="channel_post"):
    return {"update_id": uid, kind: {"chat": {"id": chat_id, "type": "channel"}, "text": text}}


def chat_id_of(db, owner):
    s = json.loads(db.execute("SELECT settings FROM users WHERE owner_id=?", (owner,)).fetchone()[0])
    return s.get("notify", {}).get("telegram_chat_id")


def test_parse_link_command():
    assert parse_link_command("/link ABC123") == "ABC123"
    assert parse_link_command("/link@CopyBot  abc-12_x ") == "abc-12_x"
    assert parse_link_command("hello /link ABC") is None
    assert parse_link_command("/link") is None
    assert parse_link_command("/link a b") is None
    assert parse_link_command("/link " + "a" * 100) is None
    assert parse_link_command(None) is None


def test_link_flow(db, server):
    add_user(db, "a.near", {"ntfy_topic": "t", "still_active_weekly": False})
    db.execute("INSERT INTO telegram_links VALUES ('CODE1', 'a.near', ?)", (11 * DAY,))
    server.responses = [(200, {"ok": True, "result": [update(41, "/link CODE1")]}, {})]
    n, _ = make(db, server)
    n.poll_telegram()
    assert chat_id_of(db, "a.near") == "-100555"
    s = json.loads(db.execute("SELECT settings FROM users").fetchone()[0])
    assert s["notify"]["ntfy_topic"] == "t" and s["notify"]["still_active_weekly"] is False
    assert db.execute("SELECT count(*) FROM telegram_links").fetchone()[0] == 0
    replies = tg_reqs(server)
    assert len(replies) == 1 and json.loads(replies[0]["body"])["chat_id"] == -100555
    # next poll confirms offset
    n.poll_telegram()
    gu = tg_reqs(server, "getUpdates")
    assert json.loads(gu[-1]["body"])["offset"] == 42


def test_link_without_notify_key(db, server):
    add_user(db, "a.near")
    db.execute("INSERT INTO telegram_links VALUES ('C', 'a.near', ?)", (11 * DAY,))
    server.responses = [(200, {"ok": True, "result": [update(1, "/link C", kind="message")]}, {})]
    n, _ = make(db, server)
    n.poll_telegram()
    assert chat_id_of(db, "a.near") == "-100555"


def test_expired_code_rejected(db, server):
    add_user(db, "a.near")
    db.execute("INSERT INTO telegram_links VALUES ('OLD', 'a.near', ?)", (DAY,))
    server.responses = [(200, {"ok": True, "result": [update(1, "/link OLD")]}, {})]
    n, _ = make(db, server)
    n.poll_telegram()
    assert chat_id_of(db, "a.near") is None
    assert tg_reqs(server) == []  # no reply: don't help code guessing


def test_unknown_code_and_noise_ignored(db, server):
    add_user(db, "a.near")
    server.responses = [(200, {"ok": True, "result": [
        update(1, "/link NOPE"), update(2, "hi"), {"update_id": 3}, {"update_id": 4, "channel_post": {"chat": {}}},
    ]}, {})]
    n, _ = make(db, server)
    n.poll_telegram()
    assert chat_id_of(db, "a.near") is None
    n.poll_telegram()
    assert json.loads(tg_reqs(server, "getUpdates")[-1]["body"])["offset"] == 5


def test_no_token_no_poll(db, server):
    n, _ = make(db, server, token=None)
    n.poll_telegram()
    assert server.requests == []


def test_getupdates_error_is_survived(db, server):
    server.default = (500, {}, {})
    n, _ = make(db, server)
    n.poll_telegram()  # no raise
    assert all(r["path"] == f"/bot{TOKEN}/getUpdates" for r in server.requests)
