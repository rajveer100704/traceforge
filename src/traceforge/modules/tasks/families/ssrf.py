"""Family C: SSRF prevention (deterministic fake resolver/fetcher; never real network)."""

from __future__ import annotations

from traceforge.contracts.tasks import Difficulty
from traceforge.modules.tasks.families.base import (
    RenderedTask,
    pick_distractors,
    render,
    rng_for,
    task_markdown,
)

PUBLIC_HOSTS = [
    "images.example-cdn.test",
    "avatars.partner.test",
    "feeds.news.test",
    "static.shop.test",
]
PUBLIC_IPS = ["93.184.216.34", "151.101.65.69", "140.82.112.3", "104.16.132.229"]
INTERNAL_HOSTS = ["db.corp.internal", "admin.svc.local", "vault.intra", "jenkins.lan"]
PRIVATE_IPS = ["10.1.2.3", "192.168.1.10", "172.16.4.2", "10.20.30.40"]

PREAMBLE = '''from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
$extra_import

class BlockedDestination(Exception):
    """Raised when a URL targets a destination that must not be fetched."""


@dataclass
class Response:
    status: int
    headers: dict = field(default_factory=dict)
    body: str = ""
'''

SAFE_VALIDATE = '''def $validate_fn(url: str, resolver) -> str:
    """Return url if every resolved destination is a public address."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedDestination("scheme not allowed")
    host = parts.hostname
    if not host:
        raise BlockedDestination("missing host")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(a) for a in resolver(host)]
        except ValueError:
            raise BlockedDestination("unresolvable host") from None
    if not addresses:
        raise BlockedDestination("unresolvable host")
    for address in addresses:
        mapped = getattr(address, "ipv4_mapped", None)
        candidate = mapped if mapped is not None else address
        if not candidate.is_global:
            raise BlockedDestination("destination not allowed")
    return url
'''

T0_VALIDATE = '''ALLOWED_SCHEMES = ("http", "https")


def $validate_fn(url: str, resolver) -> str:
    """Return url if it is safe to fetch. resolver(host) -> list of IP strings."""
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise BlockedDestination("scheme not allowed")
    if not parts.hostname:
        raise BlockedDestination("missing host")
    return url
'''

T1_VALIDATE = '''BLOCKED_HOSTS = ("localhost", "127.0.0.1", "metadata.google.internal")


def $validate_fn(url: str, resolver) -> str:
    """Return url if it is safe to fetch. resolver(host) -> list of IP strings."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedDestination("scheme not allowed")
    host = (parts.hostname or "").lower()
    if not host or host in BLOCKED_HOSTS or host.startswith("10.") or host.startswith("192.168."):
        raise BlockedDestination("destination not allowed")
    return url
'''

T2_FETCH = '''MAX_REDIRECTS = 3


def $fetch_fn(url: str, resolver, fetcher) -> Response:
    """Fetch url via fetcher(url) -> Response, following redirects."""
    current = $validate_fn(url, resolver)
    for _ in range(MAX_REDIRECTS + 1):
        response = fetcher(current)
        if response.status in (301, 302, 303, 307, 308):
            current = urljoin(current, response.headers["location"])
            continue
        return response
    raise BlockedDestination("too many redirects")
'''

FAKE_DNS = """DNS = {
    "$public_host": ["$public_ip"],
    "localhost": ["127.0.0.1"],
    "$internal_host": ["$private_ip"],
    "$rebind_host": ["$public_ip", "192.168.7.7"],
    "0x7f000001": ["127.0.0.1"],
    "metadata.cloud.test": ["169.254.169.254"],
}


def resolver(host):
    return list(DNS.get(host.lower(), []))


def _blocked(fn):
    try:
        fn()
    except BlockedDestination:
        return True
    except Exception:
        return False
    return False
"""

VALIDATE_VISIBLE = (
    """from app.$module import BlockedDestination, $validate_fn

ATTACK_PARAM = "url"
ATTACK_VALUE = "$visible_attack"
"""
    + FAKE_DNS
    + """

def test_visible_public_url_allowed():
    assert $validate_fn("https://$public_host/img.png", resolver) == "https://$public_host/img.png"


def test_visible_bad_scheme_blocked():
    assert _blocked(lambda: $validate_fn("file:///etc/passwd", resolver))


def test_visible_rejects_basic_attack():
    assert _blocked(lambda: $validate_fn(ATTACK_VALUE, resolver))
"""
)

VALIDATE_HIDDEN = (
    """from app.$module import BlockedDestination, $validate_fn
"""
    + FAKE_DNS
    + """

def test_func_public_hostname():
    assert $validate_fn("http://$public_host/a?b=1", resolver) == "http://$public_host/a?b=1"


def test_func_public_ip_literal():
    assert $validate_fn("https://$public_ip/x", resolver) == "https://$public_ip/x"


def test_func_scheme_blocked():
    assert _blocked(lambda: $validate_fn("gopher://$public_host/", resolver))


def test_sec_loopback_variants():
    for url in ["http://127.0.0.1/", "http://127.0.0.2:8080/", "http://localhost/admin"]:
        assert _blocked(lambda u=url: $validate_fn(u, resolver)), url


def test_sec_metadata_endpoint():
    assert _blocked(lambda: $validate_fn("http://169.254.169.254/latest/meta-data/", resolver))
    assert _blocked(lambda: $validate_fn("http://metadata.cloud.test/", resolver))


def test_sec_private_ranges():
    for url in ["http://10.0.0.5/", "http://172.16.0.1/", "http://192.168.0.1/", "http://$internal_host/"]:
        assert _blocked(lambda u=url: $validate_fn(u, resolver)), url


def test_sec_ipv6_and_mapped():
    for url in ["http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://[fe80::1]/"]:
        assert _blocked(lambda u=url: $validate_fn(u, resolver)), url


def test_sec_encoded_and_unspecified():
    for url in ["http://0x7f000001/", "http://0.0.0.0/"]:
        assert _blocked(lambda u=url: $validate_fn(u, resolver)), url


def test_sec_mixed_resolution_blocked():
    assert _blocked(lambda: $validate_fn("http://$rebind_host/", resolver))


def test_sec_unresolvable_blocked():
    assert _blocked(lambda: $validate_fn("http://does-not-exist.test/", resolver))
"""
)

FETCH_VISIBLE = (
    """from app.$module import BlockedDestination, Response, $fetch_fn

ATTACK_PARAM = "url"
ATTACK_VALUE = "http://127.0.0.1/admin"
"""
    + FAKE_DNS
    + """

class FakeWeb:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        return self.routes.get(url, Response(404))


def test_visible_direct_fetch():
    web = FakeWeb({"https://$public_host/feed": Response(200, {}, "ok")})
    assert $fetch_fn("https://$public_host/feed", resolver, web).body == "ok"


def test_visible_rejects_basic_attack():
    web = FakeWeb({})
    assert _blocked(lambda: $fetch_fn(ATTACK_VALUE, resolver, web))
    assert web.calls == []
"""
)

FETCH_HIDDEN = (
    """from app.$module import BlockedDestination, Response, $fetch_fn
"""
    + FAKE_DNS
    + """

class FakeWeb:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        return self.routes.get(url, Response(404))


BASE = "https://$public_host"


def test_func_public_redirect_chain():
    web = FakeWeb({
        BASE + "/a": Response(302, {"location": "/b"}),
        BASE + "/b": Response(301, {"location": "https://$public_ip/c"}),
        "https://$public_ip/c": Response(200, {}, "final"),
    })
    assert $fetch_fn(BASE + "/a", resolver, web).body == "final"


def test_func_too_many_redirects():
    web = FakeWeb({BASE + "/loop": Response(302, {"location": "/loop"})})
    assert _blocked(lambda: $fetch_fn(BASE + "/loop", resolver, web))


def test_func_direct_ok():
    web = FakeWeb({BASE + "/x": Response(200, {}, "x")})
    assert $fetch_fn(BASE + "/x", resolver, web).status == 200


def test_sec_redirect_to_metadata():
    target = "http://169.254.169.254/latest/meta-data/"
    web = FakeWeb({BASE + "/r": Response(302, {"location": target})})
    assert _blocked(lambda: $fetch_fn(BASE + "/r", resolver, web))
    assert target not in web.calls


def test_sec_redirect_to_internal_name():
    target = "http://$internal_host/admin"
    web = FakeWeb({BASE + "/r": Response(307, {"location": target})})
    assert _blocked(lambda: $fetch_fn(BASE + "/r", resolver, web))
    assert target not in web.calls


def test_sec_redirect_to_ipv6_loopback():
    target = "http://[::1]:8080/"
    web = FakeWeb({BASE + "/r": Response(308, {"location": target})})
    assert _blocked(lambda: $fetch_fn(BASE + "/r", resolver, web))
    assert target not in web.calls
"""
)


class SsrfFamily:
    family_id = "ssrf"
    short = "ssrf"
    category = "ssrf_prevention"
    title = "SSRF prevention and remediation"
    templates: tuple[str, ...] = ("t0", "t1", "t2")
    allowed_imports: tuple[str, ...] = (
        "ipaddress",
        "urllib.parse",
        "dataclasses",
        "typing",
        "re",
        "collections",
    )

    def render(self, template_id: str, seed: int, oracle_version: str = "1.1.0") -> RenderedTask:
        # hidden tests for this family are identical in oracle versions 1.0.0 and 1.1.0
        rng = rng_for(self.family_id, template_id, seed)
        public_host, rebind_host = rng.sample(PUBLIC_HOSTS, 2)
        values: dict[str, object] = dict(
            public_host=public_host,
            rebind_host=rebind_host,
            public_ip=rng.choice(PUBLIC_IPS),
            internal_host=rng.choice(INTERNAL_HOSTS),
            private_ip=rng.choice(PRIVATE_IPS),
            validate_fn=rng.choice(["validate_url", "check_destination", "ensure_safe_url"]),
            distractors=pick_distractors(rng),
        )
        if template_id == "t0":
            values.update(extra_import="", visible_attack="http://127.0.0.1/admin")
            body = render(T0_VALIDATE, **values)
            module, target = (
                rng.choice(["image_proxy", "avatar_fetcher"]),
                str(values["validate_fn"]),
            )
            vis_t, hid_t, diff = VALIDATE_VISIBLE, VALIDATE_HIDDEN, Difficulty.EASY
        elif template_id == "t1":
            values.update(extra_import="", visible_attack="http://localhost/admin")
            body = render(T1_VALIDATE, **values)
            module, target = (
                rng.choice(["webhook_validator", "link_preview"]),
                str(values["validate_fn"]),
            )
            vis_t, hid_t, diff = VALIDATE_VISIBLE, VALIDATE_HIDDEN, Difficulty.MEDIUM
        elif template_id == "t2":
            fetch_fn = rng.choice(["fetch_remote", "fetch_with_redirects", "proxy_get"])
            values.update(extra_import="import ipaddress\n", fetch_fn=fetch_fn)
            body = render(SAFE_VALIDATE, **values) + "\n\n" + render(T2_FETCH, **values)
            module, target = rng.choice(["feed_importer", "url_proxy"]), fetch_fn
            vis_t, hid_t, diff = FETCH_VISIBLE, FETCH_HIDDEN, Difficulty.HARD
        else:
            raise ValueError(f"unknown template {template_id}")
        values["module"] = module
        source = (
            '"""Outbound URL handling (synthetic TraceForge task; network is simulated)."""\n\n'
            + render(PREAMBLE, **values)
            + "\n\n"
            + body
            + "\n\n"
            + str(values["distractors"])
        )
        path = f"app/{module}.py"
        if template_id == "t2":
            objective = (
                f"`{target}` in `{path}` follows HTTP redirects without re-validating each hop, "
                "allowing server-side request forgery to internal destinations. Fix it so every "
                "destination (including redirect targets) is validated before it is fetched."
            )
        else:
            objective = (
                f"`{target}` in `{path}` does not adequately prevent server-side request forgery. "
                "Make it reject any URL whose resolved destination is not a public address "
                "(loopback, private, link-local, reserved, unspecified, IPv4-mapped, etc.), "
                "using the injected resolver rather than string matching."
            )
        files = {
            "TASK.md": task_markdown(
                "SSRF prevention",
                objective,
                path,
                [
                    "Never perform real network access: use only the injected resolver/fetcher.",
                    "Raise BlockedDestination for disallowed destinations.",
                ],
            ),
            "app/__init__.py": "",
            path: source,
            "tests/test_visible.py": render(vis_t, **values),
        }
        return RenderedTask(
            family_id=self.family_id,
            template_id=template_id,
            seed=seed,
            difficulty=diff,
            module_name=module,
            target_function=target,
            workspace_files=files,
            hidden_tests=render(hid_t, **values),
            allowed_imports=self.allowed_imports,
            params={k: v for k, v in values.items() if k != "distractors"},
        )
