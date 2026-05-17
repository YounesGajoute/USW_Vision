"""WSGI shim so Engine.IO sees PATH_INFO '/socket.io/' (required by python-engineio)."""


class EngineIoPathNormalizeMiddleware:
    """
    python-engineio WSGIApp only handles PATH_INFO that startswith '/socket.io/'.
    Proxies (e.g. Next.js rewrites) often send '/socket.io' without a trailing slash,
    so the Engine.IO handler is skipped and Flask returns 404.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO') or ''
        if path == '/socket.io':
            environ = environ.copy()
            environ['PATH_INFO'] = '/socket.io/'
        return self.wsgi_app(environ, start_response)
