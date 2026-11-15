#!/usr/bin/env python3
"""Chạy server hoặc CLI client; chỉ dùng Python standard library."""
import argparse
import os
import sys

from Error import GLPError, error_message


def main():
    parser = argparse.ArgumentParser(description="GLP chat qua TCP/TLS, client CLI")
    parser.add_argument("mode", choices=["server", "client"])
    parser.add_argument("--host", default=os.environ.get("GLP_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("GLP_PORT", "8443")))
    parser.add_argument("--download-dir", default="downloads", help="Thư mục tải file (client)")
    args = parser.parse_args()
    try:
        if args.mode == "server":
            from server.server import start_server
            start_server(host=args.host, port=args.port)
        else:
            from client.client_cli import CLI
            return CLI(args.host, args.port, download_dir=args.download_dir).run()
    except (GLPError, OSError) as error:
        print(error_message(str(error)), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
