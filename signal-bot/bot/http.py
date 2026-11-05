"""Small HTTP helper with retries, timeouts and a browser-like User-Agent."""
import time

import requests

_session = requests.Session()
_session.headers["User-Agent"] = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def get(url, params=None, timeout=15, retries=2, as_json=True):
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = _session.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                last_err = RuntimeError(f"429 rate limited: {url}")
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json() if as_json else r.text
        except (requests.RequestException, ValueError) as e:
            last_err = e
            time.sleep(1 + attempt)
    raise last_err


def post(url, json=None, timeout=15):
    r = _session.post(url, json=json, timeout=timeout)
    r.raise_for_status()
    return r.json()
