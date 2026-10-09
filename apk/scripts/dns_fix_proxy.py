"""
Tiny local HTTPS proxy for networks whose DNS server blocks amazonaws.com.

`flet build apk` downloads its Android wheels (msgpack, ...) from an Amazon S3 host. Some corporate
resolvers answer 0.0.0.0 for every *.amazonaws.com name, so pip retries for minutes and the build
looks frozen. This proxy resolves such names through a public DNS server instead and tunnels the
connection (HTTP CONNECT). Only pip is pointed at it, nothing else on the machine changes.

    python scripts/dns_fix_proxy.py                 # listens on 127.0.0.1:8899
    set PIP_PROXY=http://127.0.0.1:8899             # in the shell that runs `flet build apk`

scripts/build_apk.ps1 starts and stops it for you when -DnsFix is passed.
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import random
import socket
import struct

PUBLIC_DNS = ("8.8.8.8", "9.9.9.9")
_SINKHOLES = {"0.0.0.0", "127.0.0.1"}
_cache: dict[str, str] = {}


def build_query(name: str, qid: int | None = None) -> bytes:
    qid = random.randint(0, 0xFFFF) if qid is None else qid
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    labels = b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.rstrip(".").split("."))
    return header + labels + b"\x00" + struct.pack(">HH", 1, 1)  # type A, class IN


def _skip_name(data: bytes, pos: int) -> int:
    while True:
        length = data[pos]
        if length == 0:
            return pos + 1
        if length & 0xC0 == 0xC0:
            return pos + 2
        pos += 1 + length


def parse_a_records(data: bytes) -> list[str]:
    _, flags, qd, an, _, _ = struct.unpack(">HHHHHH", data[:12])
    if flags & 0x000F:  # non-zero RCODE
        return []
    pos = 12
    for _ in range(qd):
        pos = _skip_name(data, pos) + 4
    found: list[str] = []
    for _ in range(an):
        pos = _skip_name(data, pos)
        rtype, _, _, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
        pos += 10
        if rtype == 1 and rdlen == 4:
            found.append(socket.inet_ntoa(data[pos:pos + 4]))
        pos += rdlen
    return found


def query_public_dns(name: str, servers: tuple[str, ...] = PUBLIC_DNS, timeout: float = 3.0) -> list[str]:
    for server in servers:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            try:
                sock.sendto(build_query(name), (server, 53))
                data, _ = sock.recvfrom(2048)
                ips = parse_a_records(data)
                if ips:
                    return ips
            except (OSError, struct.error, IndexError):
                continue
    return []


async def resolve(host: str) -> str:
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if host in _cache:
        return _cache[host]
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
        ips = [i[4][0] for i in infos if i[4][0] not in _SINKHOLES]
    except OSError:
        ips = []
    if not ips:
        ips = await loop.run_in_executor(None, query_public_dns, host)
    if not ips:
        raise OSError(f"cannot resolve {host}")
    _cache[host] = ips[0]
    return ips[0]


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError, OSError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
    try:
        request = await asyncio.wait_for(client_r.readuntil(b"\r\n\r\n"), 30)
        first = request.split(b"\r\n", 1)[0].decode("latin-1")
        method, target, _ = first.split(" ", 2)
        if method.upper() != "CONNECT":
            client_w.write(b"HTTP/1.1 501 Only CONNECT is supported\r\nConnection: close\r\n\r\n")
            await client_w.drain()
            return
        host, _, port = target.rpartition(":")
        ip = await resolve(host.strip("[]"))
        remote_r, remote_w = await asyncio.wait_for(asyncio.open_connection(ip, int(port)), 20)
        client_w.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await client_w.drain()
        await asyncio.gather(_pipe(client_r, remote_w), _pipe(remote_r, client_w))
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError) as exc:
        try:
            client_w.write(f"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n{exc}".encode("latin-1", "replace"))
            await client_w.drain()
        except OSError:
            pass
    finally:
        try:
            client_w.close()
        except OSError:
            pass


async def serve(host: str, port: int) -> None:
    server = await asyncio.start_server(handle, host, port)
    print(f"dns_fix_proxy listening on {host}:{port} (public DNS {', '.join(PUBLIC_DNS)})", flush=True)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8899)
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.host, args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
