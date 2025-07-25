import signal
from server import Server
from client import Client

from prompt_toolkit.shortcuts import message_dialog

from rich import print as rprint
import yaml 
import sys
import os

import asyncio
from typing import Dict, Any, List, Optional

CONFIG = "radtinconf.yml"

async def main() -> None:
    args: List[str] = sys.argv
    if len(args) < 2:
        print("☢️ Radioactive Tinapay: The Deadliest Bread in CTF History")
        print(f"Usage: {args[0]} mode [options]")
        print("\nMode:")
        print("    server               Act as an RTC server (requires -l flag)")
        print("    client               Connect to an RTC server as client (requires -h flag)")
        print("\nOptions:")
        print("    -h IP[:PORT]         Connects to RTC host (port 1107 by default)")
        print("    -l IP[:PORT]         Listen in IP/PORT as an RTC host")
        print(f"    -c file              Use configurations from file (uses {CONFIG} default)")
        sys.exit(1)

    if not os.path.isfile(CONFIG):
        print(f"[+] ERROR: Could not find config file: {CONFIG}!")
        sys.exit(1)
    
    # Load config
    conf: Dict[str, Any] = yaml.safe_load(open(CONFIG, "r").read())

    mode: str = args[1]   # 'server' or 'client'
    opts: List[str] = args[2:]  # options

    # Override default configuration file
    if "-c" in opts:
        try:
            conf = opts[opts.index("-c")+1]
            if not os.path.isfile(conf):
                rprint(f"[red bold][+] ERROR[/red bold]: Could not find config file: {conf}!")
                sys.exit(1)
        except IndexError:
            rprint("[red bold][+] ERROR[/red bold]: Missing config file value!")
            sys.exit(1)
    
    # Run in server mode
    if mode == "server":
        try:
            host: str = opts[opts.index("-l")+1]
            await Server(host, conf).start()
        except IndexError:
            rprint("[red bold][+] ERROR[/red bold]: Host value is missing!")
            sys.exit(1)
        except ValueError:
            rprint("[red bold][+] ERROR[/red bold]: Host flag is missing!")
            sys.exit(1)
    # Connect to a server as client mode
    elif mode == "client":
        try:
            host: str = opts[opts.index("-h")+1]
            await Client(host, conf).run()
        except IndexError:
            rprint("[red bold][+] ERROR[/red bold]: Client value is missing!")
            sys.exit(1)
        except ValueError:
            rprint("[red bold][+] ERROR[/red bold]: Client flag is missing!")
            sys.exit(1)
    else:
        rprint("[red bold][+] ERROR[/red bold]: Unrecognized mode!")
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        message_dialog(
            title='Force shut down received',
            text='Use /stop command to gracefully exit next time.').run()
        try:
            sys.exit(130)
        except SystemExit:
            os._exit(130)
