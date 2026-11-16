#!/usr/bin/env python3
import argparse
import os
import sys

from Error import GLPError, error_message
from discovery import DISCOVERY_PORT, DISCOVERY_TIMEOUT, discover


def main():
    parser = argparse.ArgumentParser(description="GLP chat qua TCP/TLS, client CLI")
    parser.add_argument("mode", choices=["server", "client"])
    parser.add_argument("--host", default=os.environ.get("GLP_HOST"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("GLP_PORT", "8443")))
    parser.add_argument("--discover", action="store_true", help="Bật UDP discovery trên LAN")
    parser.add_argument("--discovery-port", type=int, default=DISCOVERY_PORT, help="Cổng UDP discovery (mặc định 8444)")
    parser.add_argument("--download-dir", default="downloads", help="Thư mục tải file (client)")
    args = parser.parse_args()
    if not 1 <= args.discovery_port <= 65535:
        parser.error("--discovery-port phải từ 1 đến 65535")
    discovery_port = args.discovery_port if args.discover else None
    default_host = "0.0.0.0" if args.mode == "server" and args.discover else "localhost"
    host = default_host if args.host is None else args.host
    try:
        if args.mode == "server":
            from server.server import start_server
            start_server(host=host, port=args.port, discovery_port=discovery_port)
        else:
            from client.client_cli import CLI
            port = args.port
            if args.discover:
                print(f"[*] Đang nghe UDP {discovery_port} tối đa {DISCOVERY_TIMEOUT:g} giây", flush=True)
                host, port = discover(port=discovery_port)
                print(f"[*] Tìm thấy GLP server tại {host}:{port}", flush=True)
            return CLI(host, port, download_dir=args.download_dir).run()
    except (GLPError, OSError) as error:
        print(error_message(str(error)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
