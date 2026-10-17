# A small asyncio HTTP/1.1 client: enough for JSON APIs (POST/GET, chunked or sized bodies, TLS).
import asyncio, json
from jade import fs


class HTTPError(Exception):
    def __init__(self, status, body, headers=None):
        super().__init__("HTTP %d: %s" % (status, body[:200]))
        self.status = status
        self.body = body
        self.headers = headers or {}


def split_url(url):
    proto, rest = url.split("://", 1)
    hostport, _, path = rest.partition("/")
    host, _, port = hostport.partition(":")
    tls = proto == "https"
    return host, int(port) if port else (443 if tls else 80), "/" + path, tls


_ssl_ctx = None


def ssl_context(verify=False, cafile="/jade/ca.pem"):
    global _ssl_ctx
    if _ssl_ctx is None:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if verify:
            with fs.open(cafile, "rb") as f:
                ctx.load_verify_locations(cadata=f.read())
            ctx.verify_mode = ssl.CERT_REQUIRED
        else:
            ctx.verify_mode = ssl.CERT_NONE
        _ssl_ctx = ctx
    return _ssl_ctx


async def _readline(r):
    line = await r.readline()
    if not line:
        raise OSError("connection closed")
    return line


async def _request(method, url, body, headers, verify):
    host, port, path, tls = split_url(url)
    r, w = await asyncio.open_connection(host, port, ssl=ssl_context(verify) if tls else None)
    try:
        hdrs = {"Host": host if port in (80, 443) else "%s:%d" % (host, port), "Connection": "close",
                "User-Agent": "JadeOS"}
        hdrs.update(headers or {})
        if body is not None:
            hdrs["Content-Length"] = str(len(body))
        head = "%s %s HTTP/1.1\r\n" % (method, path) + "".join("%s: %s\r\n" % kv for kv in hdrs.items()) + "\r\n"
        w.write(head.encode())
        if body:
            w.write(body)
        await w.drain()
        status_line = (await _readline(r)).decode()
        status = int(status_line.split(" ", 2)[1])
        rh = {}
        while True:
            line = (await _readline(r)).decode().strip()
            if not line:
                break
            k, _, v = line.partition(":")
            rh[k.strip().lower()] = v.strip()
        chunks = []
        if rh.get("transfer-encoding", "").lower() == "chunked":
            while True:
                size = int((await _readline(r)).decode().split(";")[0].strip(), 16)
                if size == 0:
                    break
                chunks.append(await r.readexactly(size))
                await _readline(r)
        elif "content-length" in rh:
            n = int(rh["content-length"])
            if n:
                chunks.append(await r.readexactly(n))
        else:
            while True:
                part = await r.read(1024)
                if not part:
                    break
                chunks.append(part)
        return status, rh, b"".join(chunks)
    finally:
        try:
            w.close()
            await w.wait_closed()
        except Exception:
            pass


async def request(method, url, data=None, headers=None, timeout=60, verify=False):
    body = None
    headers = dict(headers or {})
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    status, rh, raw = await asyncio.wait_for(_request(method, url, body, headers, verify), timeout)
    text = raw.decode()
    if status >= 400:
        raise HTTPError(status, text, rh)
    try:
        return json.loads(text) if text else None
    except ValueError:
        return text
