"""Small urllib POST with retry/backoff. Error messages never contain the URL (it may hold a secret)."""

import json
import time
import urllib.error
import urllib.request

MAX_WAIT = 60.0


class SendError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


class TransientError(SendError):
    pass


class PermanentError(SendError):
    pass


def _retry_after(headers, body) -> float | None:
    v = None
    if isinstance(body, dict):
        params = body.get("parameters")
        if isinstance(params, dict):
            v = params.get("retry_after")
    if v is None and headers is not None:
        v = headers.get("Retry-After")
    try:
        return min(max(float(v), 0.0), MAX_WAIT) if v is not None else None
    except (TypeError, ValueError):
        return None


def _json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8")) if raw else None
    except (ValueError, UnicodeDecodeError):
        return None


def post(url: str, data: bytes, headers: dict, *, timeout: float = 10.0, retries: int = 3,
         backoff: float = 1.0, sleep=time.sleep):
    """POST; returns (status, parsed_json_or_None). Retries 429, 5xx and network errors.
    Raises PermanentError on other 4xx, TransientError when retries run out."""
    last: SendError | None = None
    for attempt in range(retries + 1):
        wait = backoff * (2 ** attempt)
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, _json(resp.read())
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                body = _json(e.read())
            except OSError:
                body = None
            if status == 429:
                ra = _retry_after(e.headers, body)
                wait = ra if ra is not None else wait
                last = TransientError(f"http {status}", status)
            elif status >= 500:
                last = TransientError(f"http {status}", status)
            else:
                raise PermanentError(f"http {status}", status) from None
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            # str(URLError) can include host but not path; keep only the class name.
            last = TransientError(f"network {type(e).__name__}")
        if attempt < retries:
            sleep(min(wait, MAX_WAIT))
    raise last  # type: ignore[misc]
