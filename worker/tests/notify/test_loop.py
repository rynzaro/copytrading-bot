import json
import logging

from copytrade_worker.notify.config import Config
from copytrade_worker.notify.loop import Notifier

from .conftest import add_activity, add_user

TOKEN = "123456:SECRET-BOT-TOKEN"
TOPIC = "topic-SECRET-abc"
HOST_TOPIC = "host-SECRET-topic"
DAY = 86_400_000


def make(db, server, now=10 * DAY, **kw):
    cfg = Config(
        ntfy_url=server.url,
        telegram_token=kw.pop("token", TOKEN),
        telegram_api=server.url,
        host_ntfy_topic=kw.pop("host_topic", HOST_TOPIC),
        host_telegram_chat_id=kw.pop("host_chat", None),
        **kw,
    )
    clock = {"now": now}
    n = Notifier(db, cfg, sleep=lambda s: None, now=lambda: clock["now"])
    return n, clock


def notified(db, aid):
    return db.execute("SELECT notified_at FROM activity WHERE id=?", (aid,)).fetchone()[0]


def ntfy_reqs(server):
    return [r for r in server.requests if not r["path"].startswith("/bot")]


def tg_reqs(server, method="sendMessage"):
    return [r for r in server.requests if r["path"].endswith("/" + method)]


def test_routes_to_ntfy_and_telegram(db, server):
    add_user(db, "a.near", {"ntfy_topic": TOPIC, "telegram_chat_id": "-100777"})
    aid = add_activity(db, "a.near", "buy_filled", {"token_id": "t.near"})
    n, clock = make(db, server)
    assert n.deliver_pending() == 1
    assert notified(db, aid) == clock["now"]
    nt = ntfy_reqs(server)
    assert len(nt) == 1 and nt[0]["path"] == "/" + TOPIC and b"t.near" in nt[0]["body"]
    tg = tg_reqs(server)
    assert len(tg) == 1 and tg[0]["path"] == f"/bot{TOKEN}/sendMessage"
    body = json.loads(tg[0]["body"])
    assert body["chat_id"] == "-100777" and "t.near" in body["text"] and "parse_mode" not in body


def test_only_ntfy(db, server):
    add_user(db, "a.near", {"ntfy_topic": TOPIC})
    add_activity(db, "a.near", "gas_80")
    n, _ = make(db, server)
    n.deliver_pending()
    assert len(ntfy_reqs(server)) == 1 and tg_reqs(server) == []


def test_no_channels_marks_notified(db, server):
    add_user(db, "a.near", {})
    aid = add_activity(db, "a.near", "buy_skipped", {"reason": "x"})
    n, _ = make(db, server)
    n.deliver_pending()
    assert notified(db, aid) is not None
    assert server.requests == []


def test_telegram_without_bot_token_skipped(db, server):
    add_user(db, "a.near", {"telegram_chat_id": "1"})
    aid = add_activity(db, "a.near", "buy_filled")
    n, _ = make(db, server, token=None)
    n.deliver_pending()
    assert server.requests == [] and notified(db, aid) is not None


def test_system_rows_go_to_host(db, server):
    aid = add_activity(db, None, "system", {"text": "feed down"})
    n, _ = make(db, server, host_chat="-1009")
    n.deliver_pending()
    assert [r["path"] for r in ntfy_reqs(server)] == ["/" + HOST_TOPIC]
    assert json.loads(tg_reqs(server)[0]["body"])["chat_id"] == "-1009"
    assert notified(db, aid) is not None


def test_users_isolated(db, server):
    add_user(db, "a.near", {"ntfy_topic": "ta"})
    add_user(db, "b.near", {"ntfy_topic": "tb"})
    add_activity(db, "b.near", "buy_filled")
    n, _ = make(db, server)
    n.deliver_pending()
    assert [r["path"] for r in server.requests] == ["/tb"]


def test_unknown_owner_marked(db, server):
    aid = add_activity(db, "ghost.near", "buy_filled")
    n, _ = make(db, server)
    n.deliver_pending()
    assert notified(db, aid) is not None and server.requests == []


def test_already_notified_not_resent(db, server):
    add_user(db, "a.near", {"ntfy_topic": TOPIC})
    aid = add_activity(db, "a.near", "buy_filled")
    db.execute("UPDATE activity SET notified_at=5 WHERE id=?", (aid,))
    n, _ = make(db, server)
    assert n.deliver_pending() == 0 and server.requests == []


def test_all_fail_transient_stays_pending_then_gives_up(db, server):
    add_user(db, "a.near", {"ntfy_topic": TOPIC})
    aid = add_activity(db, "a.near", "buy_filled", created_at=10 * DAY)
    server.default = (500, {}, {})
    n, clock = make(db, server)
    n.deliver_pending()
    assert notified(db, aid) is None
    clock["now"] += DAY + 1
    n.deliver_pending()
    assert notified(db, aid) is not None


def test_permanent_failure_marks(db, server):
    add_user(db, "a.near", {"ntfy_topic": TOPIC})
    aid = add_activity(db, "a.near", "buy_filled", created_at=10 * DAY)
    server.default = (400, {}, {})
    n, _ = make(db, server)
    n.deliver_pending()
    assert notified(db, aid) is not None and len(server.requests) == 1


def test_partial_success_marks(db, server):
    add_user(db, "a.near", {"ntfy_topic": TOPIC, "telegram_chat_id": "1"})
    aid = add_activity(db, "a.near", "buy_filled", created_at=10 * DAY)
    server.responses = [(200, {}, {})]  # ntfy ok
    server.default = (500, {}, {})  # telegram fails
    n, _ = make(db, server)
    n.deliver_pending()
    assert notified(db, aid) is not None


def test_bad_settings_json_does_not_crash(db, server):
    db.execute("INSERT INTO users(owner_id, settings, created_at) VALUES ('a.near', 'not json', 0)")
    aid = add_activity(db, "a.near", "buy_filled")
    n, _ = make(db, server)
    n.deliver_pending()
    assert notified(db, aid) is not None


def test_no_secret_in_logs(db, server, caplog):
    caplog.set_level(logging.DEBUG)
    add_user(db, "a.near", {"ntfy_topic": TOPIC, "telegram_chat_id": "1"})
    add_activity(db, "a.near", "buy_filled")
    add_activity(db, None, "system")
    server.default = (500, {"description": TOPIC}, {})
    n, clock = make(db, server)
    n.run_once()
    server.default = (403, {}, {})
    add_activity(db, "a.near", "gas_80")
    n.run_once()
    server.default = (200, {"ok": True, "result": []}, {})
    clock["now"] += 30 * DAY
    n.run_once()
    for secret in (TOKEN, "SECRET", TOPIC, HOST_TOPIC):
        assert secret not in caplog.text
    assert "notify" in caplog.text  # something was logged
