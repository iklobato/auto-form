#!/usr/bin/env python3
"""auto-form: scan internal job portals, match roles to profile, and apply.

Architecture (composition root in `CLI.run()`):

    CLI ─ orchestrates ─→ Orchestrator ─ runs many ─→ PortalRunner ─→ Agent
                                                      │
                                                      ├ TaskBuilder        (renders task.md)
                                                      ├ CredentialsResolver (yaml + env)
                                                      ├ AgentActions       (registers tools)
                                                      ├ StepCallback       (per-step log + screenshot)
                                                      ├ JsonlTracker × 2   (applied / attempted)
                                                      ├ ApplicationRecorder
                                                      └ YamlStore          (persists new credentials)

    AppLogger threads through everything for structured logging + events.jsonl.
"""

import argparse
import asyncio
import base64
import fcntl
import inspect
import json
import logging
import logging.handlers
import os
import re
import secrets
import socket
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
import yaml
from browser_use import Agent
from browser_use.agent.views import ActionResult
from browser_use.browser import BrowserSession
from browser_use.browser.profile import BrowserProfile
from browser_use.llm.openrouter.chat import ChatOpenRouter
from dotenv import load_dotenv

load_dotenv()


# ── Paths ─────────────────────────────────────────────────────────────────────


class Paths:
    """All filesystem locations the app reads or writes."""

    ROOT = Path(__file__).parent
    KNOWLEDGE = ROOT / "knowledge"
    TASK_TEMPLATE = ROOT / "prompts" / "task.md"
    SCREENSHOTS = ROOT / "screenshots"
    PROFILES = ROOT / "browser_profiles"
    LOGS = ROOT / "logs"
    APPLICATIONS = ROOT / "applications"
    APPLIED = ROOT / "applied.jsonl"
    ATTEMPTED = ROOT / "attempted.jsonl"
    NOTES = KNOWLEDGE / "07_agent_notes.md"


# ── Options ───────────────────────────────────────────────────────────────────


@dataclass
class RunOptions:
    """Per-run knobs passed from the CLI down to PortalRunner."""

    dry_run: bool = False
    headless: bool = False
    fast: bool = False
    max_actions: int = 5
    filter_keyword: str = ""
    note: str = ""
    resume_path: str = ""

    def resume_for(self, system: dict) -> str:
        """A portal's own resume_path overrides the run-wide one.

        The prompt and the upload allowlist must agree, or the agent is told to
        upload a file browser_use then refuses.
        """
        return expand_env(system.get("resume_path", "")) or self.resume_path


# ── Logging ───────────────────────────────────────────────────────────────────


class _SuppressFilter(logging.Filter):
    """Drops log records whose message contains any of the given substrings."""

    def __init__(self, *fragments: str):
        super().__init__()
        self._fragments = fragments

    def filter(self, record: logging.LogRecord) -> bool:
        return not any(f in record.getMessage() for f in self._fragments)


class AppLogger:
    """Configures stdout + rotating file handlers and writes the events JSONL."""

    NOISE_FRAGMENTS = (
        "sensitive_data",
        "Frame with the given frameId is not found",
        "CDP request ax_tree failed",
        "Exception in background task [get_ax_tree]",
    )
    QUIET_LIBS = (
        "browser_use",
        "BrowserSession",
        "Agent",
        "utils",
        "httpx",
        "asyncio",
        "playwright",
    )
    FILTERED_LIBS = ("Agent", "browser_use", "BrowserSession", "utils")

    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or time.strftime("%Y%m%d_%H%M%S")
        Paths.LOGS.mkdir(exist_ok=True)
        self.log_file = Paths.LOGS / f"{self.run_id}.log"
        self.events_file = Paths.LOGS / "events.jsonl"
        self._console = self._setup()
        self.main().info("Log file: %s", self.log_file)

    def _setup(self) -> logging.StreamHandler:
        fmt = logging.Formatter(
            fmt="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
            datefmt="%H:%M:%S",
        )
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        console.setLevel(logging.INFO)

        file_handler = logging.handlers.RotatingFileHandler(
            self.log_file, maxBytes=50 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(logging.DEBUG)

        logging.basicConfig(level=logging.DEBUG, handlers=[console, file_handler])

        for lib in self.QUIET_LIBS:
            logging.getLogger(lib).setLevel(logging.WARNING)

        noise = _SuppressFilter(*self.NOISE_FRAGMENTS)
        for lib in self.FILTERED_LIBS:
            logging.getLogger(lib).addFilter(noise)
        return console

    def main(self) -> logging.Logger:
        return logging.getLogger("auto-form")

    def portal(self, portal_name: str) -> logging.Logger:
        return logging.getLogger(f"auto-form.{portal_name}")

    def set_verbose(self) -> None:
        """DEBUG level for our own loggers (auto-form.*); browser_use stays at WARNING."""
        self._console.setLevel(logging.DEBUG)

    def set_trace(self) -> None:
        """Maximum verbosity: DEBUG everywhere, including browser_use internals."""
        self._console.setLevel(logging.DEBUG)
        for lib in (
            "browser_use",
            "BrowserSession",
            "Agent",
            "utils",
            "httpx",
            "playwright",
        ):
            logging.getLogger(lib).setLevel(logging.DEBUG)

    def emit(self, event_type: str, **kwargs) -> None:
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "event": event_type,
            **kwargs,
        }
        with open(self.events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Helpers ───────────────────────────────────────────────────────────────────


def expand_env(value: str) -> str:
    """Expand ${VARS} in a string. Empty input returns empty string."""
    return os.path.expandvars(value) if value else ""


# ── YAML I/O ──────────────────────────────────────────────────────────────────


class YamlStore:
    """Read + atomic write of a systems YAML file (works for both `systems:` and `sources:` schemas)."""

    _lock = asyncio.Lock()

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        return yaml.safe_load(self.path.read_text())

    async def update_credentials(
        self, system_name: str, username: str, password: str
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._update_credentials, system_name, username, password
            )

    def _update_credentials(
        self, system_name: str, username: str, password: str
    ) -> None:
        # The asyncio lock only covers this process; flock also serializes
        # separate `fill.py --system X` runs sharing the same yaml.
        lock_path = self.path.with_name(self.path.name + ".lock")
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            raw = self.load()
            key = "systems" if "systems" in raw else "sources"
            for system in raw.get(key) or []:
                if system.get("name") == system_name:
                    creds = system.setdefault("credentials", {})
                    creds["username"] = username
                    creds["password"] = password
                    creds.pop("username_env", None)
                    creds.pop("password_env", None)
                    break
            self._replace(
                yaml.dump(
                    raw,
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                    width=120,
                )
            )

    def _replace(self, text: str) -> None:
        """Write to a sibling temp file, then rename, so a crash never truncates the yaml."""
        mode = self.path.stat().st_mode & 0o777
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise


# ── Knowledge base ────────────────────────────────────────────────────────────


class KnowledgeBase:
    """Concatenates all .md files in the knowledge directory into one prompt section."""

    def __init__(
        self, dir_path: Path = Paths.KNOWLEDGE, log: logging.Logger | None = None
    ):
        self.dir = dir_path
        self.log = log or logging.getLogger("auto-form")
        self._cache: str | None = None

    def load(self) -> str:
        if self._cache is not None:
            return self._cache
        files = sorted(f for f in self.dir.glob("*.md") if f.name != "README.md")
        self.log.info("Loading knowledge base — %d files", len(files))
        sections = []
        for f in files:
            content = f.read_text().strip()
            self.log.debug("  knowledge file: %s (%d chars)", f.name, len(content))
            sections.append(content)
        self._cache = "\n\n---\n\n".join(sections)
        self.log.info(
            "Knowledge base ready — %d total chars across %d files",
            len(self._cache),
            len(files),
        )
        return self._cache


# ── Credentials ───────────────────────────────────────────────────────────────


class CredentialsResolver:
    """Resolves portal credentials. Direct yaml values take priority over env-var refs."""

    PLACEHOLDERS = (
        ("<username>", "username", "username_env"),
        ("<password>", "password", "password_env"),
    )

    def __init__(self, log: logging.Logger):
        self.log = log

    def resolve(self, cred_config: dict) -> dict[str, str]:
        creds: dict[str, str] = {}
        for placeholder, direct_key, env_key_name in self.PLACEHOLDERS:
            env_key = cred_config.get(env_key_name)
            direct_val = cred_config.get(direct_key)
            env_val = os.getenv(env_key) if env_key else None
            val = direct_val or env_val
            if val:
                creds[placeholder] = val
                source = f"yaml ({direct_key})" if direct_val else env_key
                self.log.debug("  credential %s loaded from %s", placeholder, source)
            elif env_key:
                self.log.warning("  %s not set — login field will be skipped", env_key)
        self.log.info("  credentials loaded: %s", list(creds.keys()))
        return creds


# ── Task builder ──────────────────────────────────────────────────────────────

DEFAULT_FILTER_NOTES = (
    "Skip any listing whose TITLE contains these words — do NOT open it: "
    "Product Manager, Product Owner, Designer, UX, UI, Graphic, Marketing, Sales, "
    "Recruiter, HR, Finance, Accounting, Legal, Operations, Customer Success, Support, "
    "Data Analyst, QA, iOS, Android, Mobile. "
    "Only open listings whose title clearly suggests a backend, software engineering, "
    "data engineering, DevOps, AI/ML, infrastructure, or cloud role."
)


class TaskBuilder:
    """Renders the task.md prompt template for one portal."""

    # The browser blocks submits in a dry run anyway (DryRunGuard); this keeps the
    # agent from burning steps retrying a submit that can never go through.
    DRY_RUN_NOTICE = (
        "DRY RUN. This is a test run. Fill each application form completely, but do "
        "NOT click the final submit/apply/send button, and do not create accounts. "
        "Any submit is blocked by the browser and will fail. When a form is filled, "
        "call mark_applied for it (nothing is recorded in a dry run) and move on."
    )

    def __init__(
        self,
        template_path: Path = Paths.TASK_TEMPLATE,
        log: logging.Logger | None = None,
    ):
        self.template_path = template_path
        self.log = log or logging.getLogger("auto-form")

    def build(self, system: dict, knowledge: str, opts: RunOptions) -> str:
        url = expand_env(system.get("url", ""))
        login_url = expand_env(system.get("login_url", ""))
        search_url = expand_env(system.get("search_url", "")) or url
        primary_url = search_url if search_url and search_url != url else url

        login_line = self._login_line(system, login_url, url, primary_url)
        search_url_line = (
            f"Start your search at: {search_url}\n"
            if search_url and search_url != primary_url
            else ""
        )
        filter_line = self._filter_line(opts)
        effective_resume = opts.resume_for(system)

        substitutions = {
            "url": primary_url,
            "knowledge": knowledge,
            "login_line": login_line,
            "search_url_line": search_url_line,
            "best_for_line": self._best_for_line(system),
            "search_instructions": system.get(
                "search_instructions", "Browse all listed roles."
            ).strip(),
            "filter_notes_line": self._filter_notes_line(system),
            "application_notes_line": self._application_notes_line(system),
            "account_line": self._account_line(system),
            "form_hints": system.get(
                "form_hints", "Fill every visible field using my profile."
            ).strip(),
            "filter_line": filter_line,
            "note_line": f"Note: {opts.note}" if opts.note else "",
            "resume_line": (
                f"Resume upload: {effective_resume}" if effective_resume else ""
            ),
            "resume_line_inner": effective_resume or "no resume path configured",
        }
        task = self.template_path.read_text().format(**substitutions).strip()
        if opts.dry_run:
            task = f"{self.DRY_RUN_NOTICE}\n\n{task}"
        self.log.debug("  task built — %d chars", len(task))
        return task

    @staticmethod
    def _login_line(system: dict, login_url: str, url: str, primary_url: str) -> str:
        if not system.get("credentials"):
            return ""
        target = login_url or url
        return (
            f"First, go to {target}. If you are already logged in (cookies are persisted from a "
            f"previous run), skip the login form entirely and proceed. Otherwise, log in using "
            f"username <username> and password <password>. After successful login, navigate "
            f"DIRECTLY to {primary_url} (the pre-filtered search page) — do NOT go to the homepage.\n\n"
        )

    @staticmethod
    def _best_for_line(system: dict) -> str:
        bf = system.get("best_for") or []
        return f"This portal is best for: {', '.join(bf)}." if bf else ""

    @staticmethod
    def _filter_notes_line(system: dict) -> str:
        notes = (system.get("filter_notes") or DEFAULT_FILTER_NOTES).strip()
        return f"Filter guidance: {notes}" if notes else ""

    @staticmethod
    def _application_notes_line(system: dict) -> str:
        notes = (system.get("application_notes") or "").strip()
        return f"Application notes: {notes}" if notes else ""

    @staticmethod
    def _account_line(system: dict) -> str:
        ar = system.get("account_required_to_view", "")
        return f"Account required: {ar}" if ar else ""

    @staticmethod
    def _filter_line(opts: RunOptions) -> str:
        line = ""
        if opts.filter_keyword:
            line = (
                f"Only consider roles whose title or description contains "
                f'"{opts.filter_keyword}".'
            )
        return line


# ── HTTP backoff transport ────────────────────────────────────────────────────


class BackoffTransport(httpx.AsyncBaseTransport):
    """Wraps another transport, retrying 429/5xx with exponential backoff."""

    RETRY_STATUSES = (429, 500, 502, 503, 504)
    # browser_use cancels an LLM call after llm_timeout (75s for the default gemini
    # model). The slowest agent step in logs/events.jsonl (154 steps, May 2026) took
    # 19.8s, so 45s of total sleep leaves ~30s for the call that finally succeeds.
    MAX_TOTAL_WAIT_S = 45.0

    def __init__(
        self,
        wrapped: httpx.AsyncBaseTransport,
        *,
        max_retries: int = 5,
        base_wait: float = 2.0,
        max_wait: float = 60.0,
        max_total_wait: float = MAX_TOTAL_WAIT_S,
        log: logging.Logger | None = None,
    ):
        self._wrapped = wrapped
        self._max_retries = max_retries
        self._base_wait = base_wait
        self._max_wait = max_wait
        self._max_total_wait = max_total_wait
        self._log = log or logging.getLogger("auto-form")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        waited = 0.0
        for attempt in range(self._max_retries + 1):
            response = await self._wrapped.handle_async_request(request)
            if (
                response.status_code not in self.RETRY_STATUSES
                or attempt == self._max_retries
            ):
                return response
            wait = self._compute_wait(response.headers.get("retry-after"), attempt)
            if waited + wait > self._max_total_wait:
                self._log.warning(
                    "HTTP %d from %s: next wait %.1fs would pass the %.0fs retry budget, giving up",
                    response.status_code,
                    request.url,
                    wait,
                    self._max_total_wait,
                )
                return response
            # Release the pooled connection of the response we are discarding
            await response.aclose()
            self._log.warning(
                "HTTP %d from %s — retrying in %.1fs (attempt %d/%d)",
                response.status_code,
                request.url,
                wait,
                attempt + 1,
                self._max_retries,
            )
            await asyncio.sleep(wait)
            waited += wait
        return response

    def _compute_wait(self, retry_after_header: str | None, attempt: int) -> float:
        if retry_after_header:
            try:
                return min(float(retry_after_header), self._max_wait)
            except ValueError:
                self._log.debug(
                    "BackoffTransport: unparseable Retry-After=%r, "
                    "using exponential backoff",
                    retry_after_header,
                )
        return min(self._base_wait * (2**attempt), self._max_wait)

    async def aclose(self) -> None:
        await self._wrapped.aclose()


# ── LLM ───────────────────────────────────────────────────────────────────────


class LLMFactory:
    """Creates configured ChatOpenRouter clients with HTTP backoff."""

    DEFAULT_MODEL = "google/gemini-2.5-flash-lite"

    @classmethod
    def create(
        cls, model: str | None = None, log: logging.Logger | None = None
    ) -> ChatOpenRouter:
        log = log or logging.getLogger("auto-form")
        token = os.getenv("OPENROUTER_TOKEN")
        if not token:
            raise RuntimeError("OPENROUTER_TOKEN is not set in .env")
        os.environ.pop("ANTHROPIC_API_KEY", None)
        chosen = model or os.getenv("OPENROUTER_MODEL") or cls.DEFAULT_MODEL
        log.info("LLM: OpenRouter model=%s", chosen)
        transport = BackoffTransport(httpx.AsyncHTTPTransport(), log=log)
        client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=10.0),
        )
        return ChatOpenRouter(
            model=chosen, api_key=token, http_client=client, max_retries=0
        )


# ── Proxy providers ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Proxy:
    """One HTTP/SOCKS proxy. Value object: providers mint these, runner consumes."""

    host: str
    port: int
    username: str | None = None
    password: str | None = None
    scheme: str = "http"
    label: str = ""

    def __post_init__(self):
        if self.scheme.startswith("socks") and (self.username or self.password):
            raise ValueError(
                f"{self.label or 'proxy'}: Chrome cannot authenticate to SOCKS proxies; "
                "use an http proxy or an IP-allowlisted SOCKS one"
            )

    def to_browser_profile(self) -> dict:
        """Adapter to browser_use ProxySettings, keeps providers framework-agnostic."""
        return {
            "server": f"{self.scheme}://{self.host}:{self.port}",
            "username": self.username,
            "password": self.password,
        }


class ProxyProvider(Protocol):
    """One-method strategy: hand back a Proxy for a portal. Failures propagate."""

    def acquire(self, portal: str) -> Proxy: ...


class StaticProxyProvider:
    """Single fixed proxy from yaml/env: cheapest path, useful for self-hosted gateways."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        scheme: str = "http",
        username: str | None = None,
        password: str | None = None,
        label: str = "static",
    ):
        if not host:
            raise ValueError("StaticProxyProvider requires a host")
        self._proxy = Proxy(
            host=host,
            port=int(port),
            scheme=scheme,
            username=username or None,
            password=password or None,
            label=label,
        )

    def acquire(self, portal: str) -> Proxy:
        return self._proxy


class ProxyListProvider:
    """Round-robins through a file of `host:port` or `host:port:user:pass` lines."""

    def __init__(self, *, path: Path, scheme: str = "http"):
        raw = path.read_text().splitlines()
        stripped = (line.strip() for line in raw)
        lines = [line for line in stripped if line and not line.startswith("#")]
        if not lines:
            raise ValueError(f"ProxyListProvider: no entries in {path}")
        self._proxies = [self._parse(line, scheme) for line in lines]
        self._cursor = 0

    @staticmethod
    def _parse(line: str, scheme: str) -> Proxy:
        # maxsplit keeps a ':' inside the password
        parts = line.split(":", 3)
        if len(parts) == 2:
            host, port = parts
            return Proxy(host=host, port=int(port), scheme=scheme, label="list")
        if len(parts) == 4:
            host, port, user, pwd = parts
            return Proxy(
                host=host,
                port=int(port),
                scheme=scheme,
                username=user,
                password=pwd,
                label="list",
            )
        raise ValueError(
            f"ProxyListProvider: bad line {line!r}; expected host:port or host:port:user:pass"
        )

    def acquire(self, portal: str) -> Proxy:
        proxy = self._proxies[self._cursor % len(self._proxies)]
        self._cursor += 1
        return proxy


class WebshareProvider:
    """Webshare.io paid provider: round-robins over the valid proxies from the v2 API."""

    API_URL = "https://proxy.webshare.io/api/v2/proxy/list/"
    PAGE_SIZE = 100

    def __init__(
        self,
        *,
        token: str,
        mode: str = "direct",
        country: str | None = None,
        scheme: str = "http",
        http: httpx.Client | None = None,
    ):
        if not token:
            raise ValueError("WebshareProvider requires a token")
        if mode == "backbone":
            # backbone entries carry proxy_address=null; the endpoint is a shared gateway
            raise ValueError(
                "WebshareProvider supports mode 'direct' only, not 'backbone'"
            )
        self._token = token
        self._mode = mode
        self._country = country
        self._scheme = scheme
        self._http = http or httpx.Client(timeout=15.0)
        self._cursor = 0

    def acquire(self, portal: str) -> Proxy:
        params: dict[str, object] = {"mode": self._mode, "page_size": self.PAGE_SIZE}
        if self._country:
            params["country_code__in"] = self._country
        r = self._http.get(
            self.API_URL,
            params=params,
            headers={"Authorization": f"Token {self._token}"},
        )
        r.raise_for_status()
        results = [
            p
            for p in r.json().get("results") or []
            if p.get("valid", True) and p.get("proxy_address")
        ]
        if not results:
            raise RuntimeError(
                f"Webshare returned no valid proxies for portal={portal}"
            )
        p = results[self._cursor % len(results)]
        self._cursor += 1
        return Proxy(
            host=p["proxy_address"],
            port=int(p["port"]),
            username=p["username"],
            password=p["password"],
            scheme=self._scheme,
            label=f"webshare:{p.get('country_code', '?')}",
        )


class BrightDataProvider:
    """Bright Data residential/datacenter gateway: fixed host, per-zone composite username."""

    DEFAULT_HOST = "brd.superproxy.io"
    DEFAULT_PORT = 22225

    def __init__(
        self,
        *,
        customer: str,
        zone: str,
        password: str,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        country: str | None = None,
        scheme: str = "http",
    ):
        if not (customer and zone and password):
            raise ValueError("BrightDataProvider requires customer, zone, password")
        user = f"brd-customer-{customer}-zone-{zone}"
        if country:
            user += f"-country-{country}"
        self._proxy = Proxy(
            host=host,
            port=port,
            scheme=scheme,
            username=user,
            password=password,
            label=f"brightdata:{zone}",
        )

    def acquire(self, portal: str) -> Proxy:
        return self._proxy


class LoggingProxyProvider:
    """Decorator: logs every acquire so we know which proxy a portal got."""

    def __init__(self, inner: ProxyProvider, log: logging.Logger):
        self._inner = inner
        self._log = log

    def acquire(self, portal: str) -> Proxy:
        proxy = self._inner.acquire(portal)
        self._log.info(
            "proxy: portal=%s label=%s endpoint=%s:%d",
            portal,
            proxy.label,
            proxy.host,
            proxy.port,
        )
        return proxy


class CachingProxyProvider:
    """Decorator: re-uses the last acquired proxy per portal for ttl_s seconds."""

    def __init__(self, inner: ProxyProvider, *, ttl_s: float, clock=time.monotonic):
        if ttl_s <= 0:
            raise ValueError("CachingProxyProvider ttl_s must be > 0")
        self._inner = inner
        self._ttl_s = ttl_s
        self._clock = clock
        self._cache: dict[str, tuple[float, Proxy]] = {}

    def acquire(self, portal: str) -> Proxy:
        now = self._clock()
        cached = self._cache.get(portal)
        if cached and (now - cached[0]) < self._ttl_s:
            return cached[1]
        proxy = self._inner.acquire(portal)
        self._cache[portal] = (now, proxy)
        return proxy


UNSET_ENV_REF = re.compile(r"\$\{?[A-Za-z_]\w*\}?")


class ProxyProviderFactory:
    """Builds a ProxyProvider from a config dict. Unknown kinds raise, no fallbacks."""

    @classmethod
    def build(cls, config: dict | None, *, log: logging.Logger) -> ProxyProvider | None:
        if not config:
            return None
        kind = config.get("kind")
        if not kind or kind == "none":
            return None
        builders = {
            "static": cls._static,
            "list": cls._list,
            "webshare": cls._webshare,
            "brightdata": cls._brightdata,
        }
        if kind not in builders:
            raise ValueError(f"Unknown proxy kind {kind!r}; known: {sorted(builders)}")
        provider: ProxyProvider = builders[kind](config)
        if config.get("cache_s"):
            provider = CachingProxyProvider(provider, ttl_s=float(config["cache_s"]))
        return LoggingProxyProvider(provider, log)

    @staticmethod
    def _static(cfg: dict) -> ProxyProvider:
        port_raw = cfg.get("port")
        if port_raw is None:
            raise ValueError("static proxy requires 'port'")
        username = expand_env(cfg.get("username", "")) or (
            os.getenv(cfg["username_env"]) if cfg.get("username_env") else None
        )
        password = expand_env(cfg.get("password", "")) or (
            os.getenv(cfg["password_env"]) if cfg.get("password_env") else None
        )
        host = expand_env(cfg.get("host", ""))
        # expandvars leaves an unset ${VAR} as literal text, which Chrome would then use
        if "$" in host:
            raise ValueError(f"static proxy host references an unset env var: {host!r}")
        for field, value in (("username", username), ("password", password)):
            if value and UNSET_ENV_REF.fullmatch(value):
                raise ValueError(f"static proxy {field} references an unset env var")
        return StaticProxyProvider(
            host=host,
            port=int(expand_env(str(port_raw)) or port_raw),
            scheme=cfg.get("scheme", "http"),
            username=username or None,
            password=password or None,
        )

    @staticmethod
    def _list(cfg: dict) -> ProxyProvider:
        return ProxyListProvider(
            path=Path(expand_env(cfg["path"])),
            scheme=cfg.get("scheme", "http"),
        )

    @staticmethod
    def _webshare(cfg: dict) -> ProxyProvider:
        env_name = cfg.get("token_env", "WEBSHARE_TOKEN")
        token = os.getenv(env_name)
        if not token:
            raise RuntimeError(f"{env_name} not set in env")
        return WebshareProvider(
            token=token,
            mode=cfg.get("mode", "direct"),
            country=cfg.get("country"),
            scheme=cfg.get("scheme", "http"),
        )

    @staticmethod
    def _brightdata(cfg: dict) -> ProxyProvider:
        customer_env = cfg.get("customer_env", "BRIGHTDATA_CUSTOMER")
        password_env = cfg.get("password_env", "BRIGHTDATA_PASSWORD")
        customer = os.getenv(customer_env)
        password = os.getenv(password_env)
        zone = cfg.get("zone", "")
        if not (customer and password and zone):
            raise RuntimeError(
                f"BrightData requires {customer_env}, {password_env}, and zone"
            )
        return BrightDataProvider(
            customer=customer,
            zone=zone,
            password=password,
            country=cfg.get("country"),
        )


def portal_proxy_provider(
    system: dict, default: ProxyProvider | None, *, log: logging.Logger
) -> ProxyProvider | None:
    """A portal's own `proxy:` block overrides the run default. None = direct connection."""
    if "proxy" in system:
        return ProxyProviderFactory.build(system["proxy"], log=log)
    return default


# ── JSONL trackers ────────────────────────────────────────────────────────────

TRACKING_QUERY_PARAMS = frozenset(
    {
        "gh_src",
        "lever-source",
        "lever-origin",
        "ref",
        "referrer",
        "source",
        "src",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "trk",
        "trackingid",
        "refid",
        "_ga",
    }
)


def job_url_key(url: str) -> str:
    """Key for "is this the same job" checks.

    Ignores scheme, host case, a leading www., a trailing slash, the fragment and
    tracking params, so a listing reached through a utm link still matches.
    Job-id params (Greenhouse gh_jid, Workday ?jobId=) are kept.
    """
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_QUERY_PARAMS
            and not k.lower().startswith("utm_")
        ]
    )
    key = f"{host}{parts.path.rstrip('/')}"
    return f"{key}?{query}" if query else key


class JsonlTracker:
    """Thread-safe append-only JSONL store with shared/exclusive file locking."""

    def __init__(self, path: Path, log: logging.Logger | None = None):
        self.path = path
        self._lock = asyncio.Lock()
        self._log = log or logging.getLogger("auto-form")

    def load_keys(self, key: str = "job_url") -> set[str]:
        if not self.path.exists():
            return set()
        out: set[str] = set()
        skipped_malformed = 0
        skipped_missing_key = 0
        with open(self.path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                for lineno, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as e:
                        skipped_malformed += 1
                        self._log.warning(
                            "JsonlTracker: skipping malformed line %d in %s: %s",
                            lineno,
                            self.path.name,
                            e,
                        )
                        continue
                    if key not in record:
                        skipped_missing_key += 1
                        self._log.warning(
                            "JsonlTracker: line %d in %s missing key %r",
                            lineno,
                            self.path.name,
                            key,
                        )
                        continue
                    out.add(record[key])
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        self._log.debug(
            "JsonlTracker.%s: loaded %d keys (skipped %d malformed, %d missing key)",
            self.path.name,
            len(out),
            skipped_malformed,
            skipped_missing_key,
        )
        return out

    def has_job(self, job_url: str) -> bool:
        return job_url_key(job_url) in {job_url_key(u) for u in self.load_keys()}

    async def append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        async with self._lock:
            with open(self.path, "a") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        self._log.debug(
            "JsonlTracker.%s: appended record (%d bytes)", self.path.name, len(line)
        )


# ── Application records ───────────────────────────────────────────────────────


class ApplicationRecorder:
    """Writes per-portal application JSON snapshots and updates an INDEX.md table."""

    def __init__(self, root: Path = Paths.APPLICATIONS):
        self.root = root

    @staticmethod
    def _slugify(text: str, maxlen: int = 60) -> str:
        out = "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")
        while "__" in out:
            out = out.replace("__", "_")
        return out[:maxlen] or "untitled"

    def write(self, record: dict) -> Path:
        portal = record["portal"]
        portal_dir = self.root / portal
        portal_dir.mkdir(parents=True, exist_ok=True)

        ts = record["applied_at"].replace(":", "").replace("-", "").replace("T", "_")
        json_path = portal_dir / f"{ts}_{self._slugify(record['job_title'])}.json"
        json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2))

        self._append_index(portal_dir, portal, record, json_path.name)
        return json_path

    @staticmethod
    def _append_index(
        portal_dir: Path, portal: str, record: dict, json_name: str
    ) -> None:
        index = portal_dir / "INDEX.md"
        new = not index.exists()
        with open(index, "a", encoding="utf-8") as f:
            if new:
                f.write(f"# Applications — {portal}\n\n")
                f.write("| When | Title | Salary | Location | URL | Record |\n")
                f.write("|------|-------|--------|----------|-----|--------|\n")

            def esc(s: str | None) -> str:
                return (s or "—").replace("|", "\\|")

            f.write(
                f"| {record['applied_at']} | {esc(record['job_title'])} | "
                f"{esc(record.get('salary'))} | {esc(record.get('location'))} | "
                f"[link]({record['job_url']}) | [json](./{json_name}) |\n"
            )


# ── Network capture ───────────────────────────────────────────────────────────


def add_cdp_handler(cdp_client, method: str, callback) -> None:
    """Subscribe `callback` to a CDP event without evicting other subscribers.

    cdp_use keeps exactly one handler per event name, shared by every session on
    the client, so a plain `register.X.y(cb)` silently replaces browser_use's own
    watchdogs (dialog auto-accept, downloads) and our other recorders.
    """
    registry = cdp_client._event_registry
    existing = registry._handlers.get(method)
    callbacks = list(getattr(existing, "callbacks", [existing] if existing else []))
    if callback in callbacks:
        return
    callbacks.append(callback)

    async def fan_out(event, session_id=None):
        for cb in callbacks:
            try:
                result = cb(event, session_id)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # one bad subscriber must not starve the others
                logging.getLogger("auto-form").exception(
                    "CDP handler failed for %s", method
                )

    fan_out.callbacks = callbacks
    registry.register(method, fan_out)


class NetworkRecorder:
    """Captures POST/PUT/PATCH HTTP requests via CDP for audit purposes.

    Lets you verify the agent actually submitted the right values rather than
    trusting its self-report. Filters out analytics/telemetry noise. Records
    are persisted to applications/<portal>/network/<ts>_<method>.json and
    also kept in an in-memory ring buffer that mark_applied can attach to
    its application record.
    """

    NOISE_FRAGMENTS = (
        # Google analytics/ads
        "google-analytics",
        "googletagmanager",
        "doubleclick.net",
        "google.com/rmkt",
        "google.com/ccm",
        "google.com/pagead",
        "google.com/recaptcha",
        # SaaS analytics
        "segment.io",
        "segment.com",
        "mixpanel.com",
        "sentry.io",
        "amplitude",
        "fullstory",
        "hotjar",
        "posthog.com",
        "snowplowanalytics",
        "bzr.openai.com",
        # Error / RUM
        "datadoghq",
        "newrelic",
        "rollbar",
        "bam.nr-data.net",
        # Social / ad pixels
        "facebook.com/tr",
        "fbcdn.net",
        "analytics.twitter.com",
        "t.co/i/adsctp",
        "px.ads.linkedin.com",
        "analytics.tiktok.com",
        "analytics-ipv6.tiktokw",
        "tiktokw.us",
        "adroll.com",
        "criteo.com",
        "taboola.com",
        # Cloudflare anti-bot / insights / challenge
        "cdn-cgi/rum",
        "cdn-cgi/challenge",
        "cloudflareinsights",
        "cloudflare-analytics",
        "perimeterx",
        # Marketing / chat widgets
        "convertkit.com",
        "hubspot.com",
        "drift.com",
        "tawk.to",
        "qualified.com",
        "autopilothq.com",
        "intercom",
        "zendesk",
        # Generic patterns
        "/beacon",
        "/telemetry",
        "/heartbeat",
        "/collect",
        "/track/",
        "/pixel",
        "/enrich_ipv6",
    )
    INTERESTING_METHODS = ("POST", "PUT", "PATCH")
    BODY_CAP_BYTES = 8192
    RING_BUFFER_SIZE = 50

    def __init__(self, portal_name: str, app_logger: AppLogger):
        self.portal_name = portal_name
        self.app_logger = app_logger
        self.plog = app_logger.portal(portal_name)
        self.captures_dir = Paths.APPLICATIONS / portal_name / "network"
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        self.recent: list[dict] = []
        self._attached_session_ids: set[str] = set()
        # CDP can fire requestWillBeSent twice for the same request when there are
        # both main-page and sub-frame handlers — dedup on the unique requestId.
        self._seen_request_ids: set[str] = set()

    def start_job(self) -> None:
        """Forget earlier POSTs so mark_applied's evidence covers only this job."""
        self.recent.clear()

    async def attach_if_needed(self, browser_session) -> None:
        """Idempotent — call from the step callback. Subscribes to CDP Network events."""
        if browser_session is None:
            return
        cdp_session = await browser_session.get_or_create_cdp_session()
        sid = cdp_session.session_id
        if sid in self._attached_session_ids:
            return
        await cdp_session.cdp_client.send.Network.enable(session_id=sid)
        add_cdp_handler(
            cdp_session.cdp_client, "Network.requestWillBeSent", self._on_request
        )
        self._attached_session_ids.add(sid)
        self.plog.debug("NetworkRecorder attached to CDP session %s", sid[:12])

    def _on_request(self, event: dict, session_id: str | None = None) -> None:
        request_id = event.get("requestId", "")
        if request_id and request_id in self._seen_request_ids:
            return
        if request_id:
            self._seen_request_ids.add(request_id)

        req = event.get("request", {}) or {}
        method = req.get("method", "")
        url = req.get("url", "")
        if method not in self.INTERESTING_METHODS:
            return
        if any(noise in url for noise in self.NOISE_FRAGMENTS):
            return
        body = req.get("postData") or ""
        headers = req.get("headers") or {}
        capture = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "portal": self.portal_name,
            "method": method,
            "url": url,
            "body": body[: self.BODY_CAP_BYTES],
            "body_truncated": len(body) > self.BODY_CAP_BYTES,
            "body_size": len(body),
            "headers": {
                k: v
                for k, v in headers.items()
                if k.lower() in ("content-type", "origin", "referer")
            },
            "parsed": self._parse_body(body, headers),
        }
        self.recent.append(capture)
        if len(self.recent) > self.RING_BUFFER_SIZE:
            self.recent.pop(0)

        # time.strftime has no %f; the requestId keeps same-microsecond POSTs apart
        ts_compact = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_id = re.sub(r"[^\w.-]", "_", request_id)
        stem = f"{ts_compact}_{safe_id}" if safe_id else ts_compact
        path = self.captures_dir / f"{stem}_{method.lower()}.json"
        path.write_text(json.dumps(capture, ensure_ascii=False, indent=2))
        # DEBUG only — full record is on disk + attached to mark_applied. CDPDebugger
        # surfaces the response status of these POSTs at WARNING when 4xx/5xx.
        self.plog.debug("NETWORK %s %s (body %d bytes)", method, url[:80], len(body))
        self.app_logger.emit(
            "network_post",
            portal=self.portal_name,
            method=method,
            url=url,
            body_size=len(body),
            content_type=capture["headers"].get("content-type", ""),
        )

    @staticmethod
    def _parse_body(body: str, headers: dict) -> dict | list | None:
        """Try JSON or form-urlencoded parsing. Returns None on any failure."""
        if not body:
            return None
        ctype = (
            headers.get("Content-Type") or headers.get("content-type") or ""
        ).lower()
        if "json" in ctype:
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return None
        if "form-urlencoded" in ctype:
            from urllib.parse import parse_qs

            try:
                return parse_qs(body)
            except (ValueError, UnicodeDecodeError):
                return None
        return None


# ── CDP debug logger ──────────────────────────────────────────────────────────


class CDPDebugger:
    """Subscribes to extra CDP event streams for debugging visibility:

    - Network.responseReceived — pair status codes with outgoing POSTs
    - Network.loadingFailed    — network errors (DNS, CORS, ERR_CONNECTION)
    - Page.frameNavigated      — every navigation, including silent redirects
    - Target.targetCreated     — every new tab/popup opened by the page
    - Runtime.exceptionThrown  — uncaught JavaScript errors
    - Page.javascriptDialogOpening — alert/confirm/prompt dialogs (can block forms)

    Interesting events (4xx/5xx, errors, popups) log at INFO. Everything else
    goes to DEBUG, but every event is persisted to applications/<portal>/cdp.jsonl
    for offline inspection.
    """

    INTERESTING_STATUS = (400, 401, 403, 404, 405, 408, 429, 500, 502, 503, 504)

    def __init__(
        self,
        portal_name: str,
        app_logger: AppLogger,
        noise_fragments: tuple[str, ...] = (),
    ):
        self.portal_name = portal_name
        self.app_logger = app_logger
        self.plog = app_logger.portal(portal_name)
        self.noise_fragments = noise_fragments
        self.events_file = Paths.APPLICATIONS / portal_name / "cdp.jsonl"
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        self._attached_session_ids: set[str] = set()
        # Map requestId → (method, url) so responses can be paired with their POST
        self._request_index: dict[str, tuple[str, str]] = {}

    async def attach_if_needed(self, browser_session) -> None:
        if browser_session is None:
            return
        cdp_session = await browser_session.get_or_create_cdp_session()
        sid = cdp_session.session_id
        if sid in self._attached_session_ids:
            return
        client = cdp_session.cdp_client

        # Network.enable is shared with NetworkRecorder — calling twice is harmless
        await client.send.Network.enable(session_id=sid)
        await client.send.Page.enable(session_id=sid)
        await client.send.Runtime.enable(session_id=sid)
        await client.send.Target.setDiscoverTargets(
            params={"discover": True},
            session_id=sid,
        )

        for method, callback in (
            ("Network.requestWillBeSent", self._on_request_index),
            ("Network.responseReceived", self._on_response),
            ("Network.loadingFailed", self._on_loading_failed),
            ("Page.frameNavigated", self._on_navigated),
            ("Target.targetCreated", self._on_target_created),
            ("Runtime.exceptionThrown", self._on_exception),
            ("Page.javascriptDialogOpening", self._on_dialog),
        ):
            add_cdp_handler(client, method, callback)

        self._attached_session_ids.add(sid)
        self.plog.debug("CDPDebugger attached to session %s", sid[:12])

    # ── handlers ──────────────────────────────────────────────────────────

    def _is_noise(self, url: str) -> bool:
        return any(noise in url for noise in self.noise_fragments)

    def _on_request_index(self, event: dict, session_id: str | None = None) -> None:
        rid = event.get("requestId", "")
        req = event.get("request", {}) or {}
        if rid:
            self._request_index[rid] = (req.get("method", ""), req.get("url", ""))

    def _on_response(self, event: dict, session_id: str | None = None) -> None:
        rid = event.get("requestId", "")
        resp = event.get("response", {}) or {}
        url = resp.get("url", "")
        status = resp.get("status", 0)
        if self._is_noise(url):
            return
        # Pair with the request method if we recorded it
        method, _ = self._request_index.get(rid, ("?", url))
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "kind": "response",
            "method": method,
            "url": url,
            "status": status,
            "mime_type": resp.get("mimeType", ""),
        }
        self._persist(record)
        if status in self.INTERESTING_STATUS:
            self.plog.warning("HTTP %d %s %s", status, method, url[:80])
            self.app_logger.emit(
                "http_error",
                portal=self.portal_name,
                method=method,
                url=url,
                status=status,
            )
        else:
            self.plog.debug("HTTP %d %s %s", status, method, url[:80])

    def _on_loading_failed(self, event: dict, session_id: str | None = None) -> None:
        rid = event.get("requestId", "")
        method, url = self._request_index.get(rid, ("?", ""))
        if self._is_noise(url):
            return
        error = event.get("errorText", "")
        canceled = event.get("canceled", False)
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "kind": "loading_failed",
            "method": method,
            "url": url,
            "error": error,
            "canceled": canceled,
        }
        self._persist(record)
        if not canceled:
            self.plog.warning("NET FAIL %s %s — %s", method, url[:60], error)
            self.app_logger.emit(
                "network_failure",
                portal=self.portal_name,
                method=method,
                url=url,
                error=error,
            )

    def _on_navigated(self, event: dict, session_id: str | None = None) -> None:
        frame = event.get("frame", {}) or {}
        url = frame.get("url", "")
        is_main = not frame.get("parentId")  # no parentId => top-level frame
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "kind": "frame_navigated",
            "url": url,
            "is_main_frame": is_main,
        }
        self._persist(record)
        if is_main:
            self.plog.info("→ NAVIGATED %s", url[:100])

    def _on_target_created(self, event: dict, session_id: str | None = None) -> None:
        target = event.get("targetInfo", {}) or {}
        ttype = target.get("type", "")
        url = target.get("url", "")
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "kind": "target_created",
            "target_type": ttype,
            "url": url,
        }
        self._persist(record)
        if ttype in ("page", "popup"):
            self.plog.info("→ NEW %s %s", ttype.upper(), url[:80])
            self.app_logger.emit(
                "popup_created", portal=self.portal_name, target_type=ttype, url=url
            )

    def _on_exception(self, event: dict, session_id: str | None = None) -> None:
        details = event.get("exceptionDetails", {}) or {}
        text = details.get("text", "")
        exception_obj = (details.get("exception") or {}).get("description", "") or ""
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "kind": "js_exception",
            "text": text,
            "description": exception_obj[:500],
        }
        self._persist(record)
        self.plog.warning("JS ERROR %s — %s", text, exception_obj[:200])
        self.app_logger.emit(
            "js_exception",
            portal=self.portal_name,
            text=text,
            description=exception_obj[:500],
        )

    def _on_dialog(self, event: dict, session_id: str | None = None) -> None:
        dtype = event.get("type", "")
        message = event.get("message", "")
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "kind": "js_dialog",
            "dialog_type": dtype,
            "message": message,
        }
        self._persist(record)
        self.plog.warning("JS DIALOG %s: %s", dtype, message[:100])
        self.app_logger.emit(
            "js_dialog", portal=self.portal_name, dialog_type=dtype, message=message
        )

    def _persist(self, record: dict) -> None:
        with open(self.events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Dry-run guard ─────────────────────────────────────────────────────────────


class DryRunGuard:
    """Makes --dry-run unable to submit anything, whatever the agent decides.

    Pauses every request browser-wide through CDP Fetch and fails each POST, PUT
    or PATCH that is not analytics noise. Fetch is enabled on the browser target,
    not on a page session: a page-session Fetch let a POST from a cross-origin
    iframe (how Greenhouse embeds its form) reach the server in a real-Chrome test.
    """

    BLOCKED_METHODS = NetworkRecorder.INTERESTING_METHODS
    ERROR_REASON = "BlockedByClient"
    # Ashby loads the job and the form through POST /api/non-user-graphql; blocking
    # those left the application page with 0 inputs in a real-Chrome test.
    GRAPHQL_READ = re.compile(r"\s*(query\b|\{)")
    GRAPHQL_WRITE = re.compile(r"\b(mutation|subscription)\b")

    def __init__(self, portal_name: str, app_logger: AppLogger):
        self.portal_name = portal_name
        self.app_logger = app_logger
        self.plog = app_logger.portal(portal_name)
        self.blocked: list[str] = []
        self._client = None
        self._pending: set[asyncio.Task] = set()

    async def attach_if_needed(self, browser_session) -> None:
        if self._client is not None:
            return
        if browser_session is None:
            raise RuntimeError("dry-run guard: no browser session to protect yet")
        client = browser_session.cdp_client
        add_cdp_handler(client, "Fetch.requestPaused", self._on_paused)
        await client.send.Fetch.enable(
            params={"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}
        )
        self._client = client
        self.plog.info("DRY RUN: browser-wide submit block is on")

    def _on_paused(self, event: dict, session_id: str | None = None) -> None:
        request = event.get("request", {}) or {}
        method, url = request.get("method", ""), request.get("url", "")
        params: dict[str, object] = {"requestId": event["requestId"]}
        if (
            method in self.BLOCKED_METHODS
            and not any(noise in url for noise in NetworkRecorder.NOISE_FRAGMENTS)
            and not self._is_graphql_read(request)
        ):
            params["errorReason"] = self.ERROR_REASON
            self.blocked.append(url)
            self.plog.warning("DRY RUN: blocked %s %s", method, url[:100])
            self.app_logger.emit(
                "dry_run_blocked", portal=self.portal_name, method=method, url=url
            )
            command = self._client.send.Fetch.failRequest
        else:
            command = self._client.send.Fetch.continueRequest
        # Never await a CDP command inside an event handler: its reply arrives on
        # the same read loop that is running this handler.
        task = asyncio.create_task(command(params=params, session_id=session_id))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    @classmethod
    def _is_graphql_read(cls, request: dict) -> bool:
        """True only when every operation in a GraphQL body is a plain `query`.

        Anything else (a mutation, a multi-operation document that names one, a
        body we cannot parse, a body too large to inline) stays blocked.
        """
        try:
            body = json.loads(request.get("postData") or "")
        except json.JSONDecodeError:
            return False
        operations = body if isinstance(body, list) else [body]
        return bool(operations) and all(
            isinstance(op, dict)
            and cls.GRAPHQL_READ.match(op.get("query") or "")
            and not cls.GRAPHQL_WRITE.search(op.get("query") or "")
            for op in operations
        )


# ── Per-step callback ─────────────────────────────────────────────────────────


class StepCallback:
    """Async per-step hook: logs the step + persists a screenshot.

    Two log levels:
      INFO  — one-line summary: step number, truncated URL, action names
      DEBUG — full URL, full action params, next_goal, eval_prev, memory, screenshot path,
              elapsed since previous step
    """

    def __init__(
        self,
        portal_name: str,
        app_logger: AppLogger,
        network_recorder: "NetworkRecorder | None" = None,
        cdp_debugger: "CDPDebugger | None" = None,
        dry_run_guard: DryRunGuard | None = None,
    ):
        self.dry_run_guard = dry_run_guard
        self.portal_name = portal_name
        self.app_logger = app_logger
        self.plog = app_logger.portal(portal_name)
        self.shot_dir = Paths.SCREENSHOTS / portal_name
        self.shot_dir.mkdir(parents=True, exist_ok=True)
        self._last_step_at: float | None = None
        self.network_recorder = network_recorder
        self.cdp_debugger = cdp_debugger
        self.agent_ref = None  # set by PortalRunner after agent build

    async def on_step(self, browser_state, agent_output, step_num: int) -> None:
        # browser_use runs this hook between the LLM reply and executing its actions,
        # and an exception here makes it drop that step's actions. Logging and
        # screenshots must never cost the agent a step.
        if self.dry_run_guard:
            # Outside the try on purpose: if the guard cannot attach, dropping the
            # step's actions is exactly right, a dry run must not act unguarded.
            session = getattr(self.agent_ref, "browser_session", None)
            await self.dry_run_guard.attach_if_needed(session)
        try:
            await self._record_step(browser_state, agent_output, step_num)
        except Exception as exc:
            self.plog.warning(
                "step %04d: step callback failed, agent continues: %s: %s",
                step_num,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            self.app_logger.emit(
                "step_callback_error",
                portal=self.portal_name,
                step=step_num,
                error_type=type(exc).__name__,
                error=str(exc),
            )

    async def _record_step(self, browser_state, agent_output, step_num: int) -> None:
        # Lazy CDP attach — runs once on first step when browser_session exists
        session = (
            getattr(self.agent_ref, "browser_session", None) if self.agent_ref else None
        )
        if self.network_recorder:
            await self.network_recorder.attach_if_needed(session)
        if self.cdp_debugger:
            await self.cdp_debugger.attach_if_needed(session)
        now = time.monotonic()
        elapsed = (now - self._last_step_at) if self._last_step_at else None
        self._last_step_at = now

        url = getattr(browser_state, "url", "unknown") or "unknown"
        next_goal = getattr(agent_output, "next_goal", None) or ""
        eval_prev = getattr(agent_output, "evaluation_previous_goal", None) or ""
        memory = getattr(agent_output, "memory", None) or ""
        actions = self._extract_action_names(agent_output)
        action_details = self._extract_action_details(agent_output)

        # INFO — single-line summary
        timing = f"{elapsed:5.1f}s" if elapsed is not None else "  ----"
        self.plog.info(
            "step %04d [%s] | %-60s | %s",
            step_num,
            timing,
            url[:60],
            ", ".join(actions) or "—",
        )

        # DEBUG — full detail (only visible with --verbose)
        self.plog.debug("  full url    : %s", url)
        if action_details:
            for name, params in action_details:
                self.plog.debug(
                    "  action      : %s(%s)", name, self._format_params(params)
                )
        if next_goal:
            self.plog.debug("  → next_goal : %s", next_goal)
        if eval_prev:
            self.plog.debug("  ← eval_prev : %s", eval_prev)
        if memory:
            self.plog.debug("  memory      : %s", memory[:200])

        screenshot_path = None
        if browser_state.screenshot:
            ts = time.strftime("%Y%m%d_%H%M%S")
            screenshot_path = self.shot_dir / f"step{step_num:04d}_{ts}.png"
            screenshot_path.write_bytes(base64.b64decode(browser_state.screenshot))
            self.plog.debug("  screenshot  : %s", screenshot_path.name)

        self.app_logger.emit(
            "step",
            portal=self.portal_name,
            step=step_num,
            url=url,
            actions=actions,
            action_details=[
                {"name": n, "params": self._sanitize_params(p)}
                for n, p in action_details
            ],
            next_goal=next_goal,
            eval_prev=eval_prev,
            memory=memory,
            elapsed_s=round(elapsed, 2) if elapsed else None,
            screenshot=screenshot_path.name if screenshot_path else None,
        )

    @staticmethod
    def _extract_action_names(agent_output) -> list[str]:
        names: list[str] = []
        for act in getattr(agent_output, "action", []) or []:
            if hasattr(act, "model_dump"):
                names.extend(act.model_dump(exclude_none=True).keys())
            elif isinstance(act, dict):
                names.extend(k for k, v in act.items() if v is not None)
        return names

    @staticmethod
    def _extract_action_details(agent_output) -> list[tuple[str, dict]]:
        """List of (action_name, params_dict) for each action in this step."""
        details: list[tuple[str, dict]] = []
        for act in getattr(agent_output, "action", []) or []:
            if hasattr(act, "model_dump"):
                d = act.model_dump(exclude_none=True)
            elif isinstance(act, dict):
                d = {k: v for k, v in act.items() if v is not None}
            else:
                continue
            for name, params in d.items():
                details.append(
                    (name, params if isinstance(params, dict) else {"value": params})
                )
        return details

    @staticmethod
    def _format_params(params: dict) -> str:
        """Compact one-line representation, truncating long string values."""
        parts = []
        for k, v in params.items():
            if isinstance(v, str) and len(v) > 80:
                v = v[:77] + "..."
            parts.append(f"{k}={v!r}")
        return ", ".join(parts)

    @staticmethod
    def _sanitize_params(params: dict) -> dict:
        """Truncate long string values for the events.jsonl emit."""
        out = {}
        for k, v in params.items():
            if isinstance(v, str) and len(v) > 200:
                out[k] = v[:200] + "...[truncated]"
            else:
                out[k] = v
        return out


# ── Custom agent actions ──────────────────────────────────────────────────────


class AgentActions:
    """Registers all custom @tools.registry.action handlers for one portal."""

    _notes_lock = asyncio.Lock()

    SESSION_VISIT_LIMIT = (
        2  # max times a URL can be visited in this session before auto-bail
    )

    def __init__(
        self,
        agent: Agent,
        portal_name: str,
        *,
        dry_run: bool,
        yaml_store: YamlStore | None,
        applied: JsonlTracker,
        attempted: JsonlTracker,
        recorder: ApplicationRecorder,
        app_logger: AppLogger,
        network_recorder: "NetworkRecorder | None" = None,
    ):
        self.agent = agent
        self.portal_name = portal_name
        self.dry_run = dry_run
        self.yaml_store = yaml_store
        self.applied = applied
        self.attempted = attempted
        self.recorder = recorder
        self.app_logger = app_logger
        self.network_recorder = network_recorder
        self.plog = app_logger.portal(portal_name)
        self.shot_dir = Paths.SCREENSHOTS / portal_name
        self.shot_dir.mkdir(parents=True, exist_ok=True)
        # In-session visit counter — auto-bails when the agent revisits the same URL.
        self._session_visits: dict[str, int] = {}

    def register_all(self) -> None:
        self._register_check_applied()
        self._register_mark_applied()
        self._register_mark_attempted()
        self._register_screenshot()
        self._register_note()
        if self.yaml_store:
            self._register_save_credentials()

    def _register_check_applied(self) -> None:
        portal, plog, applied, attempted, app_logger = (
            self.portal_name,
            self.plog,
            self.applied,
            self.attempted,
            self.app_logger,
        )
        session_visits = self._session_visits
        visit_limit = self.SESSION_VISIT_LIMIT
        network_recorder = self.network_recorder

        @self.agent.tools.registry.action(
            "Check whether a specific job has already been applied to OR previously attempted-and-failed. "
            "Call this before clicking Apply on ANY listing. Returns one of: "
            "'already_applied' (we successfully applied — skip it), "
            "'already_attempted' (we tried and gave up before — DO NOT retry, skip it), "
            "'visited_this_session' (you've already opened this URL once or more in this run — "
            "PICK A DIFFERENT listing, do not retry this one), "
            "'not_applied' (proceed with the apply flow)."
        )
        async def check_applied(job_url: str) -> ActionResult:
            plog.debug("check_applied called: %s", job_url)

            # Always count the visit FIRST — applies even if the URL is already in
            # applied.jsonl or attempted.jsonl. This catches the sub-loop where the
            # agent re-checks the same URL repeatedly and ignores the skip signal.
            visit_key = job_url_key(job_url)
            session_visits[visit_key] = session_visits.get(visit_key, 0) + 1
            visits = session_visits[visit_key]

            if visits > visit_limit:
                plog.warning(
                    "HARD-SKIP (checked %d× this session): %s", visits, job_url
                )
                app_logger.emit(
                    "session_loop_detected", portal=portal, url=job_url, visits=visits
                )
                return ActionResult(
                    extracted_content=(
                        f"loop_detected — you have called check_applied on this exact URL "
                        f"{visits} times in this session. STOP. Pick a completely different "
                        f"listing whose URL contains different words. Do NOT click this URL again, "
                        f"do NOT scroll back to it, do NOT call check_applied on it again."
                    )
                )

            if applied.has_job(job_url):
                plog.info("SKIP (already applied) [visit %d]: %s", visits, job_url)
                app_logger.emit(
                    "already_applied", portal=portal, url=job_url, visits=visits
                )
                return ActionResult(extracted_content="already_applied")

            if attempted.has_job(job_url):
                plog.info(
                    "SKIP (already attempted, did not complete) [visit %d]: %s",
                    visits,
                    job_url,
                )
                app_logger.emit(
                    "already_attempted", portal=portal, url=job_url, visits=visits
                )
                return ActionResult(extracted_content="already_attempted")

            app_logger.emit(
                "check_applied",
                portal=portal,
                url=job_url,
                result="not_applied",
                visits=visits,
            )
            if network_recorder:
                network_recorder.start_job()
            return ActionResult(extracted_content="not_applied")

    def _register_mark_applied(self) -> None:
        portal, plog, applied, recorder, app_logger, dry_run = (
            self.portal_name,
            self.plog,
            self.applied,
            self.recorder,
            self.app_logger,
            self.dry_run,
        )
        network_recorder = self.network_recorder

        @self.agent.tools.registry.action(
            "Record that an application was successfully submitted. "
            "Call this immediately after the confirmation page appears. "
            "Pass as much context as the job page provided."
        )
        async def mark_applied(
            job_url: str,
            job_title: str,
            salary: str = "",
            location: str = "",
            seniority: str = "",
            employment_type: str = "",
            key_requirements: str = "",
            cover_letter_used: str = "",
            notes: str = "",
        ) -> ActionResult:
            if dry_run:
                plog.info("DRY RUN — would mark applied: %s", job_title)
                return ActionResult(extracted_content="dry_run — not recorded")
            record = {
                "portal": portal,
                "job_url": job_url,
                "job_title": job_title,
                "salary": salary,
                "location": location,
                "seniority": seniority,
                "employment_type": employment_type,
                "key_requirements": key_requirements,
                "cover_letter_used": cover_letter_used,
                "notes": notes,
                "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            # Attach the most recent network captures so the application file
            # contains evidence of what was actually POSTed during the apply flow.
            if network_recorder:
                record["recent_network_posts"] = network_recorder.recent[-10:]
            await applied.append(record)
            recorder.write(record)
            plog.info(
                "APPLIED: %s — %s [%s | %s]",
                job_title,
                job_url,
                salary or "no salary",
                location or "no location",
            )
            app_logger.emit(
                "applied",
                portal=portal,
                url=job_url,
                title=job_title,
                salary=salary,
                location=location,
                seniority=seniority,
            )
            return ActionResult(
                extracted_content=f"Recorded application to {portal}: {job_title}"
            )

    def _register_mark_attempted(self) -> None:
        portal, plog, attempted, app_logger, dry_run = (
            self.portal_name,
            self.plog,
            self.attempted,
            self.app_logger,
            self.dry_run,
        )

        @self.agent.tools.registry.action(
            "Record that you ATTEMPTED to apply to a job but could not complete it. "
            "Call this when you've tried 1–2 times without reaching a confirmation page, or when "
            "the apply flow requires steps you cannot perform. After calling this, MOVE ON to "
            "the next listing — do NOT retry the same URL. "
            "Reasons: 'external_redirect', 'captcha', 'email_verification', 'broken_form', "
            "'login_required', 'form_too_complex', 'other'."
        )
        async def mark_attempted(
            job_url: str, job_title: str, reason: str, notes: str = ""
        ) -> ActionResult:
            # check_applied skips attempted URLs forever, so a dry run must not record one
            if dry_run:
                plog.info(
                    "DRY RUN: would mark attempted: %s (reason=%s)", job_title, reason
                )
                return ActionResult(
                    extracted_content="dry_run: not recorded. Move on to next listing."
                )
            record = {
                "portal": portal,
                "job_url": job_url,
                "job_title": job_title,
                "reason": reason,
                "notes": notes,
                "attempted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            await attempted.append(record)
            plog.info("ATTEMPTED (gave up): %s — reason=%s", job_title, reason)
            app_logger.emit(
                "attempted",
                portal=portal,
                url=job_url,
                title=job_title,
                reason=reason,
                notes=notes,
            )
            return ActionResult(
                extracted_content=f"Recorded attempt (reason: {reason}). Move on to next listing."
            )

    def _register_screenshot(self) -> None:
        portal, plog, app_logger, shot_dir = (
            self.portal_name,
            self.plog,
            self.app_logger,
            self.shot_dir,
        )

        @self.agent.tools.registry.action(
            "Save a full-page screenshot of the current browser page to disk. "
            "You MUST call this immediately before clicking any submit, apply, or send button."
        )
        async def save_screenshot(browser_session: BrowserSession) -> ActionResult:
            page = await browser_session.get_current_page()
            if page is None:
                plog.warning("save_screenshot: no active browser page")
                return ActionResult(error="No active browser page")
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = shot_dir / f"submit_{ts}.png"
            b64_data = await page.screenshot(format="png")
            path.write_bytes(base64.b64decode(b64_data))
            plog.info("PRE-SUBMIT screenshot → %s", path.name)
            app_logger.emit(
                "screenshot", portal=portal, path=str(path), trigger="submit"
            )
            return ActionResult(extracted_content=f"Screenshot saved: {path}")

    def _register_note(self) -> None:
        portal, plog, app_logger = self.portal_name, self.plog, self.app_logger

        @self.agent.tools.registry.action(
            "Append a short note about a portal-specific quirk for future agents. "
            "Call this once after finishing all applications on this portal."
        )
        async def save_note(note: str) -> ActionResult:
            ts = time.strftime("%Y-%m-%d %H:%M")
            entry = f"\n## Portal: {portal} ({ts})\n{note.strip()}\n"
            async with AgentActions._notes_lock:
                with open(Paths.NOTES, "a") as f:
                    f.write(entry)
            plog.info("NOTE saved (%d chars): %s", len(note), note[:120])
            app_logger.emit("note", portal=portal, length=len(note), preview=note[:200])
            return ActionResult(extracted_content="Note saved.")

    def _register_save_credentials(self) -> None:
        portal, plog, app_logger, yaml_store = (
            self.portal_name,
            self.plog,
            self.app_logger,
            self.yaml_store,
        )

        @self.agent.tools.registry.action(
            "Save newly-created portal account credentials back to the systems YAML file for future runs. "
            "Call this immediately after successfully creating a new account."
        )
        async def save_credentials(username: str, password: str) -> ActionResult:
            plog.info("CREDENTIALS saved for %s (user=%s)", portal, username)
            await yaml_store.update_credentials(portal, username, password)
            app_logger.emit(
                "credentials_saved",
                portal=portal,
                yaml=str(yaml_store.path),
                username=username,
            )
            return ActionResult(extracted_content=f"Credentials saved for {portal}.")


# ── Per-portal runner ─────────────────────────────────────────────────────────


class AgentStoppedEarly(RuntimeError):
    """The agent hit max_steps or max_failures without calling `done`.

    browser_use returns the history instead of raising in that case, so without
    this the portal is reported as a success and never retried.
    """


class ProfileInUse(RuntimeError):
    """A live Chrome on this host already holds the portal's browser profile."""


class PortalRunner:
    """Runs the apply flow for one portal end-to-end."""

    PORTAL_PWD_ENV = "PORTAL_ACCOUNT_PASSWORD"
    LINKEDIN_USER_ENV = "LINKEDIN_SSO_USERNAME"
    LINKEDIN_PWD_ENV = "LINKEDIN_SSO_PASSWORD"
    MAX_FAILURES = 10
    # Chrome refuses to start if these symlinks exist, even if the original
    # process is dead. Killed runs leave them behind — clean up before launch.
    STALE_LOCK_NAMES = ("SingletonLock", "SingletonCookie", "SingletonSocket")

    def __init__(
        self,
        system: dict,
        *,
        knowledge: str,
        llm: ChatOpenRouter,
        opts: RunOptions,
        yaml_store: YamlStore | None,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied: JsonlTracker,
        attempted: JsonlTracker,
        recorder: ApplicationRecorder,
        app_logger: AppLogger,
        proxy_provider: ProxyProvider | None = None,
    ):
        self.system = system
        self.name = system["name"]
        self.url = expand_env(system.get("url", ""))
        self.knowledge = knowledge
        self.llm = llm
        self.opts = opts
        self.yaml_store = yaml_store
        self.task_builder = task_builder
        self.cred_resolver = cred_resolver
        self.applied = applied
        self.attempted = attempted
        self.recorder = recorder
        self.app_logger = app_logger
        self.proxy_provider = proxy_provider
        self.plog = app_logger.portal(self.name)
        self._network_recorder: NetworkRecorder | None = None

    async def run(self) -> str:
        mode = "DRY RUN" if self.opts.dry_run else "APPLY"
        self.app_logger.main().info(
            "━━ [%s] starting — mode=%s url=%s", self.name, mode, self.url
        )
        self.app_logger.emit("portal_start", portal=self.name, mode=mode, url=self.url)

        credentials = self._build_credentials()
        task = self.task_builder.build(self.system, self.knowledge, self.opts)
        self.plog.info("task ready — %d chars", len(task))

        # Providers may do a blocking HTTP call (Webshare); keep it off the event loop
        # so the other portals' agents keep running.
        proxy = (
            await asyncio.to_thread(self.proxy_provider.acquire, self.name)
            if self.proxy_provider is not None
            else None
        )
        agent = self._build_agent(task, credentials, proxy=proxy)
        AgentActions(
            agent,
            self.name,
            dry_run=self.opts.dry_run,
            yaml_store=self.yaml_store,
            applied=self.applied,
            attempted=self.attempted,
            recorder=self.recorder,
            app_logger=self.app_logger,
            network_recorder=self._network_recorder,
        ).register_all()

        return await self._run_agent(agent)

    def _build_credentials(self) -> dict[str, str]:
        creds: dict[str, str] = {}
        if self.system.get("credentials"):
            self.plog.info("loading credentials")
            creds = self.cred_resolver.resolve(self.system["credentials"])

        portal_pwd_source = (
            "PORTAL_ACCOUNT_PASSWORD env"
            if os.getenv(self.PORTAL_PWD_ENV)
            else "generated"
        )
        creds["<portal_password>"] = self._portal_password()
        self.plog.debug("portal password: source=%s", portal_pwd_source)

        linkedin_user = os.getenv(self.LINKEDIN_USER_ENV, "")
        linkedin_pwd = os.getenv(self.LINKEDIN_PWD_ENV, "")
        if linkedin_user and linkedin_pwd:
            creds["<linkedin_sso_email>"] = linkedin_user
            creds["<linkedin_sso_password>"] = linkedin_pwd
            self.plog.debug("LinkedIn SSO credentials loaded for %s", linkedin_user)
        else:
            self.plog.debug("LinkedIn SSO unavailable — env vars not set")

        # Names only, never values — sensitive_data placeholder names are safe to log
        self.plog.debug("sensitive_data placeholders: %s", sorted(creds.keys()))
        return creds

    def _portal_password(self) -> str:
        """Password for accounts the agent creates on this portal.

        Without the env var it is generated once and kept on disk before the agent
        sees it, so every run and retry uses the same one and an account created
        without a save_credentials call is not lost.
        """
        if from_env := os.getenv(self.PORTAL_PWD_ENV):
            return from_env
        path = Paths.PROFILES / self.name / "portal_password"
        if path.exists():
            return path.read_text().strip()
        path.parent.mkdir(parents=True, exist_ok=True)
        password = secrets.token_urlsafe(12)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(password)
        return password

    def _clean_stale_locks(self, profile_dir: Path) -> None:
        """Remove Chrome SingletonLock/Cookie/Socket symlinks left by a killed run."""
        if owner := self._live_lock_owner(profile_dir / "SingletonLock"):
            # Two Chromes on one profile can corrupt its cookies and saved logins
            raise ProfileInUse(
                f"{self.name}: browser profile {profile_dir} is in use by a running "
                f"Chrome (pid {owner}); is another fill.py run on this portal?"
            )
        cleaned = []
        for name in self.STALE_LOCK_NAMES:
            lock = profile_dir / name
            if lock.is_symlink() or lock.exists():
                try:
                    lock.unlink()
                    cleaned.append(name)
                except OSError as e:
                    self.plog.warning("could not remove stale lock %s: %s", name, e)
        if cleaned:
            self.plog.info("cleaned stale Chrome locks: %s", ", ".join(cleaned))

    @staticmethod
    def _live_lock_owner(lock: Path) -> int | None:
        """PID of the Chrome on this host that holds the lock, or None if it is stale.

        Chrome points SingletonLock at "<hostname>-<pid>".
        """
        if not lock.is_symlink():
            return None
        host, _, pid_text = os.readlink(lock).rpartition("-")
        if host != socket.gethostname() or not pid_text.isdigit():
            return None
        try:
            os.kill(int(pid_text), 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass  # alive, owned by another user
        return int(pid_text)

    def _build_agent(
        self, task: str, credentials: dict[str, str], *, proxy: Proxy | None = None
    ) -> Agent:
        portal_profile = Paths.PROFILES / self.name
        portal_profile.mkdir(parents=True, exist_ok=True)
        self._clean_stale_locks(portal_profile)
        is_first = not any(portal_profile.iterdir())
        self.plog.info(
            "browser profile: %s (%s)",
            portal_profile,
            "fresh" if is_first else "reusing saved session",
        )
        self.plog.info(
            "browser mode: %s", "headless" if self.opts.headless else "visible"
        )
        self.app_logger.emit(
            "browser_start",
            portal=self.name,
            profile=str(portal_profile),
            session="fresh" if is_first else "reused",
            headless=self.opts.headless,
        )

        network_recorder = NetworkRecorder(self.name, self.app_logger)
        cdp_debugger = CDPDebugger(
            self.name, self.app_logger, noise_fragments=NetworkRecorder.NOISE_FRAGMENTS
        )
        if self.opts.dry_run and proxy is not None and proxy.username:
            # browser_use's proxy-auth handler continues every paused request,
            # which would let submits through the dry-run guard
            raise ValueError(
                f"{self.name}: --dry-run cannot run with an authenticated proxy"
            )
        callback = StepCallback(
            self.name,
            self.app_logger,
            network_recorder=network_recorder,
            cdp_debugger=cdp_debugger,
            dry_run_guard=(
                DryRunGuard(self.name, self.app_logger) if self.opts.dry_run else None
            ),
        )
        # Files the agent is allowed to upload via browser_use's upload_file action.
        # Without this whitelist, EVERY upload attempt fails with
        # "File path X is not available." — observed as the root cause of
        # earlier Reveleer/WWR apply failures.
        allowed_uploads: list[str] = []
        resume_path = self.opts.resume_for(self.system)
        if resume_path and os.path.exists(resume_path):
            allowed_uploads.append(resume_path)
            self.plog.debug("file uploads allowed: %s", allowed_uploads)
        elif resume_path:
            self.plog.warning(
                "RESUME_PATH=%s does not exist — uploads will fail", resume_path
            )

        profile_kwargs: dict[str, object] = {
            "headless": self.opts.headless,
            "user_data_dir": str(portal_profile),
        }
        if proxy is not None:
            profile_kwargs["proxy"] = proxy.to_browser_profile()

        agent = Agent(
            task=task,
            llm=self.llm,
            browser_profile=BrowserProfile(**profile_kwargs),
            sensitive_data=credentials,
            available_file_paths=allowed_uploads or None,
            max_failures=self.MAX_FAILURES,
            max_actions_per_step=self.opts.max_actions,
            flash_mode=self.opts.fast,
            register_new_step_callback=callback.on_step,
        )
        callback.agent_ref = agent  # lets callbacks attach the recorders
        self._network_recorder = network_recorder
        self._cdp_debugger = cdp_debugger
        return agent

    async def _run_agent(self, agent: Agent) -> str:
        t0 = time.monotonic()
        self.plog.info(
            "agent running… (flash_mode=%s, max_actions_per_step=%d)",
            self.opts.fast,
            self.opts.max_actions,
        )
        history = await agent.run()
        elapsed = time.monotonic() - t0

        final = history.final_result() or "(no result)"
        steps = len(history.history) if hasattr(history, "history") else "?"
        self.plog.info("finished in %.1fs — %s steps", elapsed, steps)
        self.plog.info("final result:\n%s", final)
        self.app_logger.emit(
            "portal_done",
            portal=self.name,
            elapsed_s=round(elapsed, 1),
            steps=steps,
            result_preview=final[:300],
        )
        if not history.is_done():
            raise AgentStoppedEarly(
                f"{self.name}: agent stopped after {steps} steps without calling done"
            )
        return final


# ── Orchestrator ──────────────────────────────────────────────────────────────


class Orchestrator:
    """Runs N portals concurrently with a worker semaphore + retry on failure."""

    RETRY_BACKOFF_SECONDS = 5

    def __init__(
        self,
        systems: list[dict],
        *,
        knowledge: str,
        llm: ChatOpenRouter,
        opts: RunOptions,
        max_workers: int,
        yaml_store: YamlStore | None,
        task_builder: TaskBuilder,
        cred_resolver: CredentialsResolver,
        applied: JsonlTracker,
        attempted: JsonlTracker,
        recorder: ApplicationRecorder,
        app_logger: AppLogger,
        default_proxy_provider: ProxyProvider | None = None,
    ):
        self.systems = systems
        self.knowledge = knowledge
        self.llm = llm
        self.opts = opts
        self.max_workers = max_workers
        self.yaml_store = yaml_store
        self.task_builder = task_builder
        self.cred_resolver = cred_resolver
        self.applied = applied
        self.attempted = attempted
        self.recorder = recorder
        self.app_logger = app_logger
        self.default_proxy_provider = default_proxy_provider
        self.log = app_logger.main()
        self.active = self._select_active(systems)
        self.skipped_count = len(systems) - len(self.active)
        # Built once, up front: a bad proxy block fails before any browser starts,
        # and a retry reuses the same provider (list cursor, cache) instead of a fresh one.
        self._proxy_providers = {
            s["name"]: portal_proxy_provider(s, default_proxy_provider, log=self.log)
            for s in self.active
        }
        self._crashed: dict[str, Exception] = {}

    @staticmethod
    def _select_active(systems: list[dict]) -> list[dict]:
        active = []
        for s in systems:
            url = expand_env(s.get("url", ""))
            if url and not url.startswith("$"):
                active.append(s)
        return active

    async def run(self) -> None:
        self.log.info(
            "Running %d portal(s) — workers=%d skipped=%d fast=%s max_actions=%d",
            len(self.active),
            self.max_workers,
            self.skipped_count,
            self.opts.fast,
            self.opts.max_actions,
        )
        if self.opts.resume_path:
            self.log.info("Resume PDF: %s", self.opts.resume_path)
        self.app_logger.emit(
            "run_start",
            portals=[s["name"] for s in self.active],
            workers=self.max_workers,
            dry_run=self.opts.dry_run,
        )

        results = await self._run_batch(self.active, "pass 1")
        results = await self._retry_failures(results)
        self._summarise(results)
        if self._crashed:
            raise next(iter(self._crashed.values()))

    async def _retry_failures(
        self, results: list[tuple[str, str | Exception]]
    ) -> list[tuple[str, str | Exception]]:
        failed = {
            n for n, r in results if isinstance(r, Exception) and n not in self._crashed
        }
        if not failed:
            return results
        self.log.info("Retrying %d failed portal(s): %s", len(failed), sorted(failed))
        self.app_logger.emit("retry", portals=sorted(failed))
        await asyncio.sleep(self.RETRY_BACKOFF_SECONDS)
        retry_batch = [s for s in self.active if s["name"] in failed]
        retry_results = await self._run_batch(retry_batch, "retry")
        return [r for r in results if r[0] not in failed] + retry_results

    def _summarise(self, results: list[tuple[str, str | Exception]]) -> None:
        ok = [n for n, r in results if not isinstance(r, Exception)]
        failed = [n for n, r in results if isinstance(r, Exception)]
        self.log.info("━━━━━━━━━━━━━━━━━━━━ all portals done ━━━━━━━━━━━━━━━━━━━━")
        self.log.info("  succeeded: %d — %s", len(ok), ok)
        self.log.info("  failed:    %d — %s", len(failed), failed)
        if self._crashed:
            self.log.info("  crashed:   %s (bug, not retried)", sorted(self._crashed))
        if self.skipped_count:
            self.log.info("  skipped:   %d (URL not configured)", self.skipped_count)
        self.app_logger.emit(
            "run_done", succeeded=ok, failed=failed, skipped=self.skipped_count
        )

    async def _run_batch(
        self, batch: list[dict], label: str
    ) -> list[tuple[str, str | Exception]]:
        sem = asyncio.Semaphore(self.max_workers)
        results: list[tuple[str, str | Exception]] = []
        completed = 0

        # Failure modes we want to catch and continue past (other portals are still useful):
        # - browser_use's own runtime errors (wrapped in their own classes)
        # - Browser/CDP connection issues (TimeoutError, OSError, asyncio.CancelledError)
        # - Network errors from the LLM (httpx.HTTPError)
        # Real bugs (TypeError/AttributeError/KeyError from our code) are not retried
        # and still crash the run, but only after the other portals finish and the
        # summary is printed, so one bug does not cancel browsers mid-application.
        recoverable = (TimeoutError, OSError, RuntimeError, httpx.HTTPError)

        async def run_one(system: dict) -> tuple[str, str | Exception]:
            name = system["name"]
            async with sem:
                try:
                    return (name, await self._make_runner(system).run())
                except recoverable as exc:
                    self.log.error(
                        "[%s] failed (recoverable): %s: %s",
                        name,
                        type(exc).__name__,
                        exc,
                        exc_info=True,
                    )
                    self.app_logger.emit(
                        "portal_error",
                        portal=name,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    return (name, exc)
                except Exception as exc:
                    self.log.critical(
                        "[%s] crashed (bug): %s: %s",
                        name,
                        type(exc).__name__,
                        exc,
                        exc_info=True,
                    )
                    self.app_logger.emit(
                        "portal_crash",
                        portal=name,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                    self._crashed[name] = exc
                    return (name, exc)

        tasks = [asyncio.create_task(run_one(s)) for s in batch]
        for coro in asyncio.as_completed(tasks):
            name, result = await coro
            completed += 1
            status = "✓" if not isinstance(result, Exception) else "✗"
            self.log.info(
                "━━ [%d/%d] %s %s  (%s)",
                completed,
                len(batch),
                status,
                name,
                label,
            )
            results.append((name, result))
        return results

    def _make_runner(self, system: dict) -> PortalRunner:
        return PortalRunner(
            system,
            knowledge=self.knowledge,
            llm=self.llm,
            opts=self.opts,
            yaml_store=self.yaml_store,
            task_builder=self.task_builder,
            cred_resolver=self.cred_resolver,
            applied=self.applied,
            attempted=self.attempted,
            recorder=self.recorder,
            app_logger=self.app_logger,
            proxy_provider=self._proxy_providers[system["name"]],
        )


# ── Reports ──────────────────────────────────────────────────────────────────


class SummaryReporter:
    """Prints a per-portal application summary from disk."""

    def __init__(
        self, root: Path = Paths.APPLICATIONS, log: logging.Logger | None = None
    ):
        self.root = root
        self.log = log or logging.getLogger("auto-form")

    def print(self, systems: list[dict]) -> None:
        if not self.root.exists():
            self.log.info("No applications recorded yet — applications/ does not exist")
            return
        portal_dirs = sorted(p for p in self.root.iterdir() if p.is_dir())
        if not portal_dirs:
            self.log.info("No applications recorded yet — applications/ is empty")
            return

        descs = {s["name"]: s.get("description", "") for s in systems}
        total = 0
        self.log.info("━━━━━━━━━━━━━━━━━━━━ APPLICATION SUMMARY ━━━━━━━━━━━━━━━━━━━━")
        for portal_dir in portal_dirs:
            records = sorted(portal_dir.glob("*.json"))
            if not records:
                continue
            self.log.info(
                "\n● %s (%d application%s) — %s",
                portal_dir.name,
                len(records),
                "" if len(records) == 1 else "s",
                descs.get(portal_dir.name, ""),
            )
            for rec_path in records:
                try:
                    rec = json.loads(rec_path.read_text())
                except json.JSONDecodeError as e:
                    self.log.warning(
                        "   skipping malformed record %s: %s", rec_path.name, e
                    )
                    continue
                except OSError as e:
                    self.log.warning("   could not read %s: %s", rec_path.name, e)
                    continue
                self.log.info(
                    "   %s  %-50s  %-20s  %s",
                    rec.get("applied_at", "")[:16],
                    (rec.get("job_title") or "")[:50],
                    (rec.get("salary") or "—")[:20],
                    rec.get("location") or "—",
                )
                total += 1
        self.log.info(
            "\n━━ TOTAL: %d applications across %d portals ━━", total, len(portal_dirs)
        )


# ── CLI ──────────────────────────────────────────────────────────────────────


def positive_int(value: str) -> int:
    """argparse type: --workers 0 would make Semaphore(0) and hang the run forever."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, got {number}")
    return number


class CLI:
    """Argparse-based command-line entrypoint and composition root."""

    @staticmethod
    def _build_parser() -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(description="Internal job portals — scan and apply")
        group = p.add_mutually_exclusive_group(required=True)
        group.add_argument("--all", action="store_true", help="Run every portal")
        group.add_argument("--system", help="Run a single portal by name")
        group.add_argument(
            "--list", action="store_true", help="List registered portals"
        )
        group.add_argument(
            "--summary",
            action="store_true",
            help="Print a per-portal application summary from applications/",
        )
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="Assess matches but do not submit applications",
        )
        p.add_argument(
            "--filter",
            dest="filter_keyword",
            default="",
            help="Only consider roles containing this keyword",
        )
        p.add_argument("--note", default="", help="Extra context for this run")
        p.add_argument(
            "--workers",
            type=positive_int,
            default=5,
            help="Max parallel browser sessions when using --all (default: 5)",
        )
        p.add_argument(
            "--systems",
            default="systems.yaml",
            help="YAML file listing portals (default: systems.yaml)",
        )
        p.add_argument(
            "--headless",
            action="store_true",
            help="Run browser in headless mode (default: visible)",
        )
        p.add_argument(
            "--verbose",
            "-v",
            action="store_true",
            help="DEBUG-level logs for auto-form (full URLs, action params, agent reasoning)",
        )
        p.add_argument(
            "--trace",
            "-vv",
            action="store_true",
            help="MAX verbosity: includes browser_use, httpx, playwright internals",
        )
        p.add_argument(
            "--fast",
            action="store_true",
            help="Enable flash_mode (skips agent self-eval/thinking, ~30%% faster)",
        )
        p.add_argument(
            "--max-actions",
            type=int,
            default=5,
            help="Max actions per LLM call (default 5; raise to 8–12)",
        )
        p.add_argument(
            "--model", help="OpenRouter model id (overrides OPENROUTER_MODEL env)"
        )
        return p

    @classmethod
    def run(cls) -> None:
        args = cls._build_parser().parse_args()
        app_logger = AppLogger()
        log = app_logger.main()
        if args.trace:
            app_logger.set_trace()
            log.info(
                "TRACE mode enabled — DEBUG everywhere including browser_use internals"
            )
        elif args.verbose:
            app_logger.set_verbose()
            log.info("Verbose mode enabled — DEBUG for auto-form loggers")

        yaml_store = YamlStore(Path(args.systems).resolve())
        raw = yaml_store.load()
        systems = raw.get("systems") or raw.get("sources") or []
        default_proxy_cfg = raw.get("proxy")
        log.info("Loaded %d portals from %s", len(systems), args.systems)

        if args.list:
            cls._list_portals(systems, log)
            return
        if args.summary:
            SummaryReporter(log=log).print(systems)
            return

        opts = cls._build_opts(args)
        log.info("auto-form starting — run_id=%s", app_logger.run_id)
        if opts.resume_path:
            log.info("Resume PDF: %s", opts.resume_path)

        deps = cls._build_deps(args, log, app_logger, yaml_store, opts)
        deps["default_proxy_provider"] = ProxyProviderFactory.build(
            default_proxy_cfg,
            log=log,
        )
        app_logger.emit(
            "startup",
            run_id=app_logger.run_id,
            systems_file=str(yaml_store.path),
            dry_run=args.dry_run,
            headless=args.headless,
            workers=args.workers,
            resume=opts.resume_path,
            model=deps["model_id"],
            proxy_kind=(default_proxy_cfg or {}).get("kind") or "none",
        )

        if args.all:
            cls._run_all(systems, args.workers, opts, yaml_store, deps, app_logger, log)
        else:
            cls._run_single(
                args.system, systems, opts, yaml_store, deps, app_logger, log
            )

    @staticmethod
    def _build_opts(args: argparse.Namespace) -> RunOptions:
        return RunOptions(
            dry_run=args.dry_run,
            headless=args.headless,
            fast=args.fast,
            max_actions=args.max_actions,
            filter_keyword=args.filter_keyword,
            note=args.note,
            resume_path=os.getenv("RESUME_PATH", ""),
        )

    @staticmethod
    def _build_deps(args, log, app_logger, yaml_store, opts) -> dict:
        return {
            "knowledge": KnowledgeBase(log=log).load(),
            "llm": LLMFactory.create(model=args.model, log=log),
            "task_builder": TaskBuilder(log=log),
            "cred_resolver": CredentialsResolver(log=log),
            "applied": JsonlTracker(Paths.APPLIED),
            "attempted": JsonlTracker(Paths.ATTEMPTED),
            "recorder": ApplicationRecorder(),
            "model_id": (
                args.model or os.getenv("OPENROUTER_MODEL") or LLMFactory.DEFAULT_MODEL
            ),
        }

    @staticmethod
    def _list_portals(systems: list[dict], log: logging.Logger) -> None:
        log.info("Registered portals:")
        for s in systems:
            url = expand_env(s.get("url", ""))
            configured = "✓" if url and not url.startswith("$") else "⚠ URL not set"
            creds = "🔐" if s.get("credentials") else "  "
            log.info(
                "  %s %s %-20s — %s  [%s]",
                creds,
                configured,
                s["name"],
                s.get("description", ""),
                url,
            )

    @staticmethod
    def _run_all(systems, workers, opts, yaml_store, deps, app_logger, log) -> None:
        log.info("Mode: ALL portals (%d)", len(systems))
        asyncio.run(
            Orchestrator(
                systems,
                knowledge=deps["knowledge"],
                llm=deps["llm"],
                opts=opts,
                max_workers=workers,
                yaml_store=yaml_store,
                task_builder=deps["task_builder"],
                cred_resolver=deps["cred_resolver"],
                applied=deps["applied"],
                attempted=deps["attempted"],
                recorder=deps["recorder"],
                app_logger=app_logger,
                default_proxy_provider=deps["default_proxy_provider"],
            ).run()
        )

    @staticmethod
    def _run_single(
        system_name, systems, opts, yaml_store, deps, app_logger, log
    ) -> None:
        matches = [s for s in systems if s["name"] == system_name]
        if not matches:
            log.error(
                "Unknown system %r. Available: %s",
                system_name,
                [s["name"] for s in systems],
            )
            raise SystemExit(1)
        system = matches[0]
        url = expand_env(system.get("url", ""))
        if not url or url.startswith("$"):
            log.error("[%s] URL not configured — set the env var in .env", system_name)
            raise SystemExit(1)
        proxy_provider = portal_proxy_provider(
            system, deps["default_proxy_provider"], log=log
        )
        runner = PortalRunner(
            system,
            knowledge=deps["knowledge"],
            llm=deps["llm"],
            opts=opts,
            yaml_store=yaml_store,
            task_builder=deps["task_builder"],
            cred_resolver=deps["cred_resolver"],
            applied=deps["applied"],
            attempted=deps["attempted"],
            recorder=deps["recorder"],
            app_logger=app_logger,
            proxy_provider=proxy_provider,
        )
        asyncio.run(runner.run())


def main() -> None:
    """Entry point referenced by `[project.scripts] fill = 'fill:main'`."""
    CLI.run()


if __name__ == "__main__":
    main()
