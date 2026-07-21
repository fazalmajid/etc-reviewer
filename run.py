#!/usr/bin/env python3
import argparse
import os
import threading
import webbrowser

from etc_reviewer.app import create_app

DEFAULT_MACHINES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "machines.txt")


def main():
    parser = argparse.ArgumentParser(description="Review and commit /etc changes across machines.")
    parser.add_argument("-m", "--machines-file", default=DEFAULT_MACHINES_FILE,
                         help=f"Path to a text file listing one hostname per line (default: {DEFAULT_MACHINES_FILE})")
    parser.add_argument("-p", "--port", type=int, default=5757, help="Port to bind on localhost (default: 5757)")
    parser.add_argument("--no-browser", action="store_true", help="Don't automatically open a browser window")
    args = parser.parse_args()

    if not os.path.exists(args.machines_file):
        parser.error(f"Machines file not found: {args.machines_file}")

    app = create_app(args.machines_file)
    url = f"http://127.0.0.1:{args.port}/"

    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    print(f"etc-reviewer serving at {url}")
    print(f"Machines file: {args.machines_file}")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
