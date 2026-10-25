"""Small public-document client with per-hop DNS checks and pinned connections.

No proxy inheritance, credentials, cookies, compressed responses or arbitrary
ports. TLS still verifies the original hostname, not the pinned IP literal.
"""
from dataclasses import dataclass
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit
from urllib.parse import urlencode

from app.domain.research import canonical_url

MAX_BODY = 2 * 1024**2
_request_observer = ContextVar('public_request_observer', default=None)


class PublicHTTPError(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__('Source server returned status ' + str(status))


@contextmanager
def observe_public_requests(observer):
    token = _request_observer.set(observer)
    try:
        yield
    finally:
        _request_observer.reset(token)


def public_addresses(url):
    if not isinstance(url, str) or len(url) > 4000 or any(ord(char) <= 32 or ord(char) == 127 for char in url) or '\\' in url:
        raise ValueError('Malformed public URL')
    value = canonical_url(url)
    parsed = urlsplit(value)
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if port != (443 if parsed.scheme == 'https' else 80) or '%' in parsed.hostname:
        raise ValueError('Only standard HTTP(S) ports and plain hostnames are allowed')
    try:
        addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)))
    except OSError as exc:
        raise ValueError('Public hostname could not be resolved') from exc
    if not addresses:
        raise ValueError('No public addresses')
    # Some local VPNs synthesize benchmark-range DNS records. Do not connect
    # to them. Resolve domain names independently through a fixed, TLS-verified
    # DoH bootstrap instead; the replacement answers still pass every check.
    benchmark = (ipaddress.ip_network('198.18.0.0/15'), ipaddress.ip_network('2001:2::/48'))
    try:
        ipaddress.ip_address(parsed.hostname)
        literal = True
    except ValueError:
        literal = False
    if not literal and all(any(ipaddress.ip_address(address) in network for network in benchmark if network.version == ipaddress.ip_address(address).version) for address in addresses):
        addresses = _doh_addresses(parsed.hostname)
    for address in addresses:
        item = ipaddress.ip_address(address)
        if isinstance(item, ipaddress.IPv6Address) and (item.sixtofour or item.teredo or item.is_site_local):
            raise ValueError('IPv6 transition and site-local destinations are forbidden')
        if isinstance(item, ipaddress.IPv6Address) and item.ipv4_mapped:
            item = item.ipv4_mapped
        if not item.is_global or item.is_multicast or item.is_reserved:
            raise ValueError('Non-public network destinations are forbidden')
    return value, parsed, addresses


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address, timeout, tls):
        super().__init__(host, port=port, timeout=timeout)
        self.address, self.tls = address, tls

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)
        self._wire_socket = self.sock
        if self.tls:
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host, do_handshake_on_connect=False)
            self._wire_socket = self.sock
            self.sock.do_handshake()


def _doh_addresses(host):
    observer = _request_observer.get()
    with observer('public_dns', {'hostname': host}) if observer else nullcontext():
        return _doh_exchange(host)


def _doh_exchange(host):
    connection = _PinnedHTTP('cloudflare-dns.com', 443, '1.1.1.1', 8, True)
    try:
        connection.request('GET', '/dns-query?' + urlencode({'name': host, 'type': 'A'}), headers={'Accept': 'application/dns-json'})
        response = connection.getresponse()
        raw = response.read(16385)
        if response.status != 200 or len(raw) > 16384:
            raise ValueError('Trusted public DNS lookup failed')
        body = json.loads(raw)
        addresses = [record['data'] for record in body.get('Answer', []) if record.get('type') == 1]
        if body.get('Status') != 0 or not addresses:
            raise ValueError('Trusted public DNS returned no addresses')
        return list(dict.fromkeys(addresses))
    finally:
        connection.close()


def _exchange(parsed, address, timeout):
    observer = _request_observer.get()
    with observer('public_http', {'hostname': parsed.hostname}) if observer else nullcontext():
        return _source_exchange(parsed, address, timeout)


def _source_exchange(parsed, address, timeout):
    connection = _PinnedHTTP(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), address, timeout, parsed.scheme == 'https')
    expired = threading.Event()
    def abort():
        expired.set()
        wire = getattr(connection, '_wire_socket', None)
        if wire is not None:
            try: wire.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            wire.close()
    timer = threading.Timer(timeout, abort)
    timer.daemon = True
    timer.start()
    response = None
    try:
        target = parsed.path or '/'
        if parsed.query:
            target += '?' + parsed.query
        connection.request('GET', target, headers={'User-Agent': 'ResearchCopilot/0.1 local-study', 'Accept-Encoding': 'identity', 'Accept': 'text/html,text/plain,application/pdf'})
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        if response.status in (301, 302, 303, 307, 308):
            return response.status, headers, b''
        if response.status != 200:
            raise PublicHTTPError(response.status)
        if headers.get('content-encoding', 'identity').lower() != 'identity':
            raise ValueError('Compressed source responses are not accepted')
        if int(headers.get('content-length', '0')) > MAX_BODY:
            raise ValueError('Source exceeds 2 MiB')
        mime = headers.get('content-type', '').split(';', 1)[0].lower()
        if mime not in ('text/html', 'text/plain', 'application/pdf'):
            raise ValueError('Unsupported source content type')
        deadline = time.monotonic() + timeout
        chunks = []; size = 0
        while size <= MAX_BODY:
            if time.monotonic() >= deadline:
                raise TimeoutError('Source body deadline exceeded')
            part = response.read1(min(65536, MAX_BODY + 1 - size))
            if not part:
                break
            chunks.append(part); size += len(part)
        raw = b''.join(chunks)
        if expired.is_set():
            raise TimeoutError('Source transport deadline exceeded')
        if not raw or len(raw) > MAX_BODY:
            raise ValueError('Source body exceeds local size limit or is empty')
        return response.status, headers, raw
    finally:
        timer.cancel()
        if response is not None:
            response.close()
        connection.close()


@dataclass(frozen=True)
class FetchedSource:
    url: str
    content_type: str
    raw: bytes

    @property
    def sha256(self):
        return hashlib.sha256(self.raw).hexdigest()


def fetch_public(url, *, max_redirects=3, timeout=20):
    deadline = time.monotonic() + timeout
    for hop in range(max_redirects + 1):
        value, parsed, addresses = public_addresses(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Source fetch deadline exceeded')
        # Connect to the vetted literal IP, never resolve the hostname a second time.
        status, headers, raw = _exchange(parsed, addresses[0], min(remaining, 8))
        if status in (301, 302, 303, 307, 308):
            if hop == max_redirects or not headers.get('location'):
                raise ValueError('Source redirect limit exceeded or target missing')
            url = urljoin(value, headers['location'])
            continue
        return FetchedSource(value, headers['content-type'].split(';', 1)[0].lower(), raw)
    raise ValueError('Source redirect limit exceeded')
