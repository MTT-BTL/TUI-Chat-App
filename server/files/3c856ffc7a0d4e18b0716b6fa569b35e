#!/Users/admin/miniforge3/envs/ctfenv/bin/python
import argparse
import sys
import os


sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

def main():
    parser = argparse.ArgumentParser(description="Distributed Client-Server GLP Application")
    parser.add_argument("mode", choices=["server", "client", "hacker"], help="Server, client thường hoặc client hacker")
    parser.add_argument("--host", default=os.environ.get("GLP_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("GLP_PORT", "8443")))
    args = parser.parse_args()

    if args.mode == "server":
        print("[*] Khởi động hệ thống Server...")
        from server.server import start_server
        start_server(host=args.host, port=args.port)
        
    else:
        from client.client import GLPApp as ClientApp
        ClientApp(args.host, args.port).run()

if __name__ == "__main__":
    main()
