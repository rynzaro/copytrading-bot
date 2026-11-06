import logging

import pytest

from copytrade_worker.notify.httppost import PermanentError, TransientError, post


def test_post_ok(server):
    status, body = post(server.url + "/x", b"hi", {"Title": "t"})
    assert status == 200 and body == {"ok": True, "result": {}}
    assert server.requests[0]["body"] == b"hi"


def test_retry_then_ok(server, no_sleep):
    calls, sleep = no_sleep
    server.responses = [(500, {}, {}), (502, {}, {})]
    status, _ = post(server.url + "/x", b"", {}, sleep=sleep)
    assert status == 200
    assert len(server.requests) == 3
    assert calls == [1.0, 2.0]


def test_retry_exhausted(server, no_sleep):
    calls, sleep = no_sleep
    server.default = (503, {}, {})
    with pytest.raises(TransientError):
        post(server.url + "/x", b"", {}, retries=3, sleep=sleep)
    assert len(server.requests) == 4
    assert calls == [1.0, 2.0, 4.0]


def test_429_uses_retry_after(server, no_sleep):
    calls, sleep = no_sleep
    server.responses = [(429, {"ok": False, "parameters": {"retry_after": 7}}, {})]
    post(server.url + "/x", b"", {}, sleep=sleep)
    assert calls == [7.0]


def test_429_header_retry_after_capped(server, no_sleep):
    calls, sleep = no_sleep
    server.responses = [(429, {}, {"Retry-After": "9999"})]
    post(server.url + "/x", b"", {}, sleep=sleep)
    assert calls == [60.0]


def test_4xx_permanent_no_retry(server, no_sleep):
    calls, sleep = no_sleep
    server.default = (403, {"ok": False}, {})
    with pytest.raises(PermanentError) as e:
        post(server.url + "/x", b"", {}, sleep=sleep)
    assert e.value.status == 403
    assert len(server.requests) == 1 and calls == []


def test_connection_refused_is_transient(no_sleep):
    calls, sleep = no_sleep
    with pytest.raises(TransientError) as e:
        post("http://127.0.0.1:9/secret-topic", b"", {}, retries=1, sleep=sleep)
    assert "secret-topic" not in str(e.value)
    assert len(calls) == 1


def test_errors_do_not_carry_url(server, no_sleep, caplog):
    _, sleep = no_sleep
    server.default = (500, {}, {})
    caplog.set_level(logging.DEBUG)
    with pytest.raises(TransientError) as e:
        post(server.url + "/bot123:SECRET/sendMessage", b"", {}, retries=1, sleep=sleep)
    assert "SECRET" not in str(e.value) and "SECRET" not in repr(e.value)
    assert "SECRET" not in caplog.text
