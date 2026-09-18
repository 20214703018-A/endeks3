#!/usr/bin/env python3
"""Toplayicilar icin ortak, gozlemlenebilir HTTP erisim katmani.

Ag hatalari bos veri gibi yorumlanmaz. Her deneme HTTP durumu, hata kodu ve
deneme sayisiyla birlikte doner; cagiran kod ancak gercek veri ayrisinca
checkpoint'i BASARILI yapar.
"""

from __future__ import annotations

import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class FetchResult:
    url: str
    body: str | None
    status: int | None
    attempts: int
    error_code: str | None
    error_detail: str | None

    @property
    def ok(self) -> bool:
        return bool(self.body) and self.status is not None and 200 <= self.status < 300


def fetch_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15,
    attempts: int = 3,
    min_bytes: int = 1,
    blocked_markers: tuple[str, ...] = (),
) -> FetchResult:
    """Metni sinirli exponential backoff ile indir; hatayi asla yutma."""
    last_status = None
    last_code = None
    last_detail = None
    for attempt in range(1, max(1, attempts) + 1):
        request = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                last_status = getattr(response, "status", None) or response.getcode()
                body = response.read().decode("utf-8", errors="ignore")
                marker = next((m for m in blocked_markers if m and m in body), None)
                if marker:
                    last_code = "BLOCKED_PAGE"
                    last_detail = marker
                elif len(body) < min_bytes:
                    last_code = "BODY_TOO_SMALL"
                    last_detail = f"bytes={len(body)}"
                else:
                    return FetchResult(url, body, int(last_status), attempt, None, None)
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            last_code = f"HTTP_{exc.code}"
            last_detail = str(exc.reason)
            if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                break
        except urllib.error.URLError as exc:
            last_code = "URL_ERROR"
            last_detail = str(exc.reason)
        except TimeoutError as exc:
            last_code = "TIMEOUT"
            last_detail = str(exc)
        except Exception as exc:  # platform TLS/socket exceptions
            last_code = type(exc).__name__.upper()
            last_detail = str(exc)
        if attempt < attempts:
            time.sleep(min(4.0, (0.7 * (2 ** (attempt - 1))) + random.uniform(0.05, 0.35)))
    return FetchResult(url, None, last_status, max(1, attempts), last_code or "UNKNOWN", last_detail)
