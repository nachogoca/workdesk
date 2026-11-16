"""HTTP access (the only urllib user) and the Collector base class."""
import json
import urllib.error
import urllib.parse
import urllib.request


class ConfigError(Exception):
    pass


class HttpError(Exception):
    """Failure of one request. Never carries headers, the query string or the response body."""

    def __init__(self, status, url):
        self.status = status
        self.url = url.split("?", 1)[0]
        what = "network error" if status == 0 else f"HTTP {status}"
        super().__init__(f"{what} for {self.url}")


def build_url(url, params=None):
    if not params:
        return url
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)


def parse_query(url):
    """Return {key: first value} of a URL's query string."""
    q = urllib.parse.urlsplit(url).query
    return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}


def quote(text):
    return urllib.parse.quote(text, safe="")


def b64(text):
    import base64
    return base64.b64encode(text.encode()).decode()


class Http:
    """Real network access. Tests and demo mode replace it with FixtureHttp."""

    def __init__(self, timeout=20):
        self.timeout = timeout

    def request(self, method, url, headers=None, params=None, json_body=None, form=None):
        if not url.lower().startswith(("http://", "https://")):
            raise HttpError(0, "invalid-url")
        full = build_url(url, params)
        data = None
        hdrs = dict(headers or {})
        if json_body is not None:
            data = json.dumps(json_body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        hdrs.setdefault("User-Agent", "workdesk/0.1")
        req = urllib.request.Request(full, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            raise HttpError(e.code, url) from None
        except (urllib.error.URLError, OSError, TimeoutError):
            raise HttpError(0, url) from None
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except ValueError:
            raise HttpError(0, url) from None


class Collector:
    name = ""

    def __init__(self, cfg_section, http, env):
        self.cfg, self.http, self.env = cfg_section, http, env

    def token(self, key="token_env"):
        var = self.cfg[key]
        val = self.env.get(var)
        if not val:
            raise ConfigError(f"missing env var {var}")
        return val

    def collect(self, now):
        raise NotImplementedError

    def check(self):
        """One cheap authenticated call; returns a short success description or raises."""
        raise NotImplementedError
