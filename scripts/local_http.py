"""Origin-bound HTTP for trusted loopback services; no external proxy or redirect auth leakage."""
import base64
import http.cookiejar
import urllib.parse
import urllib.request


class UnsafeLocalRequest(ValueError):
    """A request would leave the configured local origin."""


def local_origin(url):
    """Validate without echoing URLs that could contain userinfo or credentials."""
    if not isinstance(url, str) or any(character in url for character in "\r\n\t"):
        raise UnsafeLocalRequest("Invalid local service URL")
    try:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.username is not None or parsed.password is not None):
            raise UnsafeLocalRequest("Only HTTP loopback URLs without userinfo are allowed")
        port = parsed.port if parsed.port is not None else 80
        if port < 1 or port > 65535:
            raise UnsafeLocalRequest("Invalid local service port")
    except ValueError:
        raise UnsafeLocalRequest("Invalid local service URL") from None
    return parsed.scheme, parsed.hostname, port


class _ScopedAuthorization(urllib.request.BaseHandler):
    handler_order = 400

    def __init__(self, origin, authorization):
        self.origin = origin
        self.authorization = authorization

    def http_request(self, request):
        if local_origin(request.full_url) != self.origin:
            raise UnsafeLocalRequest("Blocked request outside the configured local origin")
        if self.authorization:
            # Do not copy this header to redirects. The handler adds it again
            # only after independently validating the next request's origin.
            request.add_unredirected_header("Authorization", self.authorization)
        return request

    https_request = http_request


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin):
        self.origin = origin

    def redirect_request(self, request, response, code, message, headers, new_url):
        try:
            allowed = local_origin(new_url) == self.origin
        except UnsafeLocalRequest:
            allowed = False
        if not allowed:
            response.close()
            raise UnsafeLocalRequest("Blocked redirect outside the configured local origin")
        return super().redirect_request(request, response, code, message, headers, new_url)

    # Older supported Python versions do not define a 308 handler themselves.
    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_302


class LocalHTTPClient:
    """Authenticate only the configured origin, including during redirect handling.

    This restricts credential forwarding; it cannot authenticate an arbitrary
    process impersonating the original loopback service. Prefer owned tunnels.
    """

    def __init__(self, base_url, username=None, password=None, cookies=False):
        self.origin = local_origin(base_url)
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise UnsafeLocalRequest("The local service base URL must not include a path, query or fragment")
        if (username is None) != (password is None):
            raise ValueError("Provide both username and password, or neither")
        authorization = None
        if username is not None:
            authorization = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        handlers = [
            urllib.request.ProxyHandler({}),
            _ScopedAuthorization(self.origin, authorization),
            _SameOriginRedirect(self.origin),
        ]
        if cookies:
            handlers.append(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._opener = urllib.request.build_opener(*handlers)

    def open(self, url_or_request, data=None, timeout=30):
        url = url_or_request.full_url if isinstance(url_or_request, urllib.request.Request) else url_or_request
        if local_origin(url) != self.origin:
            raise UnsafeLocalRequest("Blocked request outside the configured local origin")
        return self._opener.open(url_or_request, data=data, timeout=timeout)
