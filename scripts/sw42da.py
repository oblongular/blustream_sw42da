"""Command-line tool for talking to an SW42DA through the integration's client.

python scripts/sw42da.py HOST status
python scripts/sw42da.py HOST send "OUT FR 02" [...]
python scripts/sw42da.py HOST listen
"""

import argparse
import asyncio
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.blustream_sw42da.protocol import (
    DEFAULT_PORT,
    SW42DAClient,
)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status", help="print the parsed STATUS")
    send = sub.add_parser("send", help="send commands and print the responses")
    send.add_argument("commands", nargs="+")
    sub.add_parser("listen", help="print source changes until interrupted")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    client = SW42DAClient(
        args.host,
        args.port,
        on_source_changed=lambda source: print(f"source changed: {source}"),
    )
    await client.connect()
    try:
        if args.action == "status":
            status = await client.status()
            for name, value in vars(status).items():
                print(f"{name}: {value}")
        elif args.action == "send":
            for command in args.commands:
                print(f"> {command}")
                for line in await client.command(command):
                    print(f"  {line}")
        else:
            print("Listening; press Ctrl-C to stop")
            while client.connected:
                await asyncio.sleep(1)
    finally:
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
