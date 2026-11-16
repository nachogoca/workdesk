"""Google Calendar: OAuth installed-app flow (loopback), busy-time read, focus-block write.

Not an items source: it is used by the planner and the brief only."""
import base64
import hashlib
import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

from ..models import iso, norm_ts, parse_iso
from .base import Collector, ConfigError, build_url, parse_query, quote

SCOPE = "https://www.googleapis.com/auth/calendar.events"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/calendar/v3"


class MemoryTokenStore:
    def __init__(self, data=None):
        self.data = data

    def load(self):
        return self.data

    def save(self, data):
        self.data = data


class FileTokenStore:
    def __init__(self, path):
        self.path = os.path.expanduser(path)

    def load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def save(self, data):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
        except BaseException:
            raise


def parse_event(raw):
    """One events.list entry -> flat dict. The only place that knows Calendar field names."""
    s, e = raw.get("start") or {}, raw.get("end") or {}
    all_day = "date" in s and "dateTime" not in s
    me = [a for a in raw.get("attendees") or [] if a.get("self")]
    return {"title": raw.get("summary") or "(no title)",
            "start": s.get("date") if all_day else norm_ts(s.get("dateTime")),
            "end": e.get("date") if all_day else norm_ts(e.get("dateTime")),
            "all_day": all_day,
            "declined": bool(me) and me[0].get("responseStatus") == "declined",
            "free": raw.get("transparency") == "transparent",
            "cancelled": raw.get("status") == "cancelled",
            "is_workdesk": ((raw.get("extendedProperties") or {}).get("private") or {}).get("workdesk") == "1"}


class GoogleCalendar(Collector):
    name = "calendar"

    def __init__(self, cfg_section, http, env, token_store=None):
        super().__init__(cfg_section, http, env)
        self.store = token_store or FileTokenStore(cfg_section["token_file"])
        self._access = None

    def client(self):
        out = []
        for key in ("client_id_env", "client_secret_env"):
            v = self.env.get(self.cfg[key])
            if not v:
                raise ConfigError(f"missing env var {self.cfg[key]}")
            out.append(v)
        return out

    def access_token(self):
        if self._access and self._access[1] > time.monotonic():
            return self._access[0]
        tok = self.store.load()
        if not tok or not tok.get("refresh_token"):
            raise ConfigError("not authorised: run 'workdesk auth google'")
        cid, secret = self.client()
        r = self.http.request("POST", TOKEN_URL, form={"client_id": cid, "client_secret": secret,
                                                       "refresh_token": tok["refresh_token"],
                                                       "grant_type": "refresh_token"})
        self._access = (r["access_token"], time.monotonic() + max(60, int(r.get("expires_in", 3600)) - 60))
        return self._access[0]

    def headers(self):
        return {"Authorization": "Bearer " + self.access_token()}

    def events_url(self):
        return f"{API}/calendars/{quote(self.cfg['calendar_id'])}/events"

    def busy(self, day, tz):
        """Meetings that block time on local date `day`: not declined, not all-day, not free, not ours."""
        z = ZoneInfo(tz)
        d0 = datetime.fromisoformat(day).replace(tzinfo=z)
        lo, hi = d0, d0 + timedelta(days=1)
        r = self.http.request("GET", self.events_url(), headers=self.headers(), params={
            "timeMin": iso(lo), "timeMax": iso(hi), "singleEvents": "true", "orderBy": "startTime",
            "maxResults": 250})
        out = []
        for raw in r.get("items", []):
            ev = parse_event(raw)
            if ev["all_day"] or ev["declined"] or ev["free"] or ev["cancelled"] or ev["is_workdesk"]:
                continue
            if not ev["start"] or not ev["end"]:
                continue
            if parse_iso(ev["end"]) <= lo or parse_iso(ev["start"]) >= hi:
                continue
            out.append({"title": ev["title"], "start": ev["start"], "end": ev["end"]})
        return out

    def insert_block(self, title, start, end, url):
        """Create the focus event (private, busy, marked workdesk=1); returns the event id."""
        body = {"summary": "Focus: " + title, "description": url or "",
                "start": {"dateTime": start}, "end": {"dateTime": end},
                "visibility": "private", "transparency": "opaque",
                "extendedProperties": {"private": {"workdesk": "1"}}}
        return self.http.request("POST", self.events_url(), headers=self.headers(), json_body=body)["id"]

    def check(self):
        self.http.request("GET", self.events_url(), headers=self.headers(), params={"maxResults": 1})
        return "calendar readable"

    def authorize(self, open_url=None, timeout=300, say=print):
        """Installed-app flow: loopback redirect on 127.0.0.1, state check, PKCE S256, offline access."""
        cid, secret = self.client()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        result = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                q = parse_query(self.path)
                if "code" in q or "error" in q:
                    result.update(q)
                    msg = b"workdesk: authorisation received. You can close this tab."
                    self.send_response(200)
                else:
                    msg = b"not found"
                    self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)

        srv = HTTPServer(("127.0.0.1", 0), Handler)
        srv.timeout = 1
        redirect = f"http://127.0.0.1:{srv.server_address[1]}"
        url = build_url(AUTH_URL, {"client_id": cid, "redirect_uri": redirect, "response_type": "code",
                                   "scope": SCOPE, "state": state, "code_challenge": challenge,
                                   "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})
        say("Open this URL to authorise workdesk (read and write calendar events):")
        say(url)
        if open_url is None:
            import webbrowser
            open_url = webbrowser.open
        threading.Thread(target=open_url, args=(url,), daemon=True).start()
        try:
            deadline = time.monotonic() + timeout
            while not result and time.monotonic() < deadline:
                srv.handle_request()
        finally:
            srv.server_close()
        if not result:
            raise ConfigError("authorisation timed out")
        if result.get("state") != state:
            raise ConfigError("authorisation failed: state mismatch")
        if "error" in result:
            raise ConfigError("authorisation failed: " + result["error"])
        r = self.http.request("POST", TOKEN_URL, form={
            "code": result["code"], "client_id": cid, "client_secret": secret, "code_verifier": verifier,
            "redirect_uri": redirect, "grant_type": "authorization_code"})
        if not r.get("refresh_token"):
            raise ConfigError("Google returned no refresh token; revoke workdesk access and retry")
        self.store.save({"refresh_token": r["refresh_token"], "scope": r.get("scope", SCOPE)})
        return True
