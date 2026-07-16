import logging
import time
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from bidmatch.config import BASE_URL, INDEX_PATH, ARTICLE_PATH

log = logging.getLogger(__name__)

USER_AGENT = (
    "BidMatch-Extractor/0.1 (+contact: cp-industries; "
    "polite static-HTML reader)"
)


def _redact(url: str, token: str) -> str:
    if not token:
        return url
    return url.replace(token, "<redacted>")


class BidMatchClient:
    def __init__(
        self,
        token: str,
        min_delay: float = 1.5,
        timeout: float = 30.0,
    ):
        self.token = token
        self.min_delay = min_delay
        self.timeout = timeout
        self._last_request_at: float = 0.0

        retry = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session = requests.Session()
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({"User-Agent": USER_AGENT})

    def index_url(self) -> str:
        return f"{BASE_URL}{INDEX_PATH}?{urlencode({'sub': self.token})}"

    def daily_url(self, doc: str) -> str:
        return f"{BASE_URL}{INDEX_PATH}?{urlencode({'doc': doc})}"

    def article_url(self, doc: str, seq: int) -> str:
        return f"{BASE_URL}{ARTICLE_PATH}?{urlencode({'doc': doc, 'seq': seq})}"

    def get(self, url: str) -> str:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        log.debug("GET %s", _redact(url, self.token))
        try:
            response = self.session.get(url, timeout=self.timeout)
            self._last_request_at = time.monotonic()
            response.raise_for_status()
        except requests.RequestException as exc:
            safe_msg = _redact(str(exc), self.token)
            raise requests.RequestException(safe_msg) from None
        return response.text
