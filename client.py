from rich.console import Console
from rich.theme import Theme
from rich.table import Table
from rich import print as rprint

from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.application import run_in_terminal

import websockets
import asyncio
from websockets.client import WebSocketClientProtocol

from random import choices
import signal
import os
import json
import time
import sys
from typing import Dict, Optional, Any, List, Union, Callable, Awaitable

PROMPT = ":(you)::> "
DEFAULT_PORT = 1107

USERNAME = os.environ.get('USER', os.environ.get('USERNAME'))

console = Console(force_terminal=True, color_system="truecolor", file=sys.stdout)
session = PromptSession(style=Style.from_dict({
    'prompt': 'fg:yellow bold',
}))

# Custom temporary nametag color for each user
user_colors: Dict[str, str] = {}

MessageData = Dict[str, Any]

class Client():
    def __init__(self, server_addr: str, conf: Dict[str, Any]) -> None:
        # Initialize client state
        self.username = None
        self.server_addr = server_addr
        self.websocket = None

        # store command methods
        self.commands: Dict[str, Callable[[List[str]], Awaitable[None]]] = {
            'flag': self.cmd_flag,
        }

        self.host: str = server_addr
        self.port: int = DEFAULT_PORT

        if ":" in server_addr:
            components = server_addr.split(":")
            self.host = components[0]
            self.port = int(components[1])
    
    async def run(self) -> None:
        """
        Main run loop for the client
        """
        await self.connect_server(self.host, self.port)
        await self.start()

    async def connect_server(self, host: str, port: int) -> None:
        """
        Connects to host server and authenticate.
        Starts asynchronous session with server

        Args:
            host (string): IP or domain of server
            port (int): port of server
        """
        payload: MessageData = {
            'type': 'auth',
            'from': USERNAME,
        }

        with console.status("☢️ Connecting to Radioactive Tinapay server...", spinner="earth"):
            try:
                # Add 60 second timeout to connection attempt
                self.websocket = await asyncio.wait_for(
                    websockets.connect(f'ws://{host}:{port}'),
                    timeout=60
                )
                await self.websocket.send(json.dumps(payload))
            except TimeoutError:
                console.print("[red bold]❌ Connection timed out[/red bold]")
                sys.exit(1)
            except Exception as e:
                console.print(f"[red bold]❌ Error during connection: {e}[/red bold]")
                sys.exit(1)

        with console.status("☢️ Waiting for server response...", spinner="simpleDotsScrolling"):
            # Wait for auth response
            while True:
                try:
                    data = await self.websocket.recv()
                    response = json.loads(data)
                    
                    if response.get('type') == 'auth':
                        if response.get('status') == 'accepted':
                            console.print("[green bold]✅ Connection accepted![/green bold]")
                            self.authenticated = True
                            self.username = response.get('from')
                            break
                        elif response.get('status') == 'rejected':
                            console.print("[red bold]❌ Connection rejected![/red bold]")
                            await self.websocket.close()
                            sys.exit(1)
                except Exception as e:
                    console.print(f"[red bold]❌ Error during authentication: {e}[/red bold]")
                    await self.websocket.close()
                    sys.exit(1)
        
        # Client banner 
        print("██████╗  █████╗ ██████╗        ████████╗██╗███╗   ██╗   ")
        print("██╔══██╗██╔══██╗██╔══██╗       ╚══██╔══╝██║████╗  ██║   ")
        print("██████╔╝███████║██║  ██║          ██║   ██║██╔██╗ ██║   ")
        print("██╔══██╗██╔══██║██║  ██║          ██║   ██║██║╚██╗██║   ")
        print("██║  ██║██║  ██║██████╔╝██╗       ██║   ██║██║ ╚████║██╗")
        print("╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚═╝       ╚═╝   ╚═╝╚═╝  ╚═══╝╚═╝")
        print("☢️ Radio-active!\n")

    async def start(self) -> None:
        """
        Starts the client's main loop
        """
        with patch_stdout():
            try:
                await asyncio.gather(
                    self.send_loop(),
                    self.recv_loop()
                )
            except asyncio.CancelledError:
                pass

    async def stop(self) -> None:
        """
        Stops the client's main loop
        """
        with console.status("☢️ Closing session", spinner="monkey"):
            if self.websocket:
                await self.websocket.close()

    async def recv_loop(self) -> None:
        """
        Asynchronous loop for receiving websocket messages
        """
        while True:
            if not self.websocket:
                break
            data = await self.websocket.recv()
            try:
                payload: MessageData = json.loads(data)

                # Handler for msg type
                if payload.get('type') == 'msg':
                    if payload.get('from') not in user_colors.keys():
                        user_colors[payload.get('from')] = '#' + ''.join(choices('4321abcdef', k=6))
                    # Print message from user with color
                    await self.new_msg(f"<[{user_colors[payload.get('from')]}]{payload.get('from')}[/]> {payload.get('content')}")
                # Handler for announce type
                elif payload.get('type') == 'announce':
                    await self.new_msg(f"[yellow bold]📢 SERVER:[/yellow bold] {payload.get('content')}")
                # Handler for auth type
                elif payload.get('type') == 'auth':
                    if payload.get('status') == 'pending':
                        await self.new_msg(f"[yellow bold]⚠️ Auth request from `{payload.get('from')}`[/yellow bold]")
                        await self.new_msg(f"[yellow]Use /auth accept {payload.get('from')} to accept[/yellow]")
                        await self.new_msg(f"[yellow]Use /auth reject {payload.get('from')} to reject[/yellow]")
                    elif payload.get('status') == 'accepted':
                        await self.new_msg(f"[green bold]✅ Connection accepted by server[/green bold]")
                    elif payload.get('status') == 'rejected':
                        await self.new_msg(f"[red bold]❌ Connection rejected by server[/red bold]")
                        await self.stop()
                # Handler for flags type
                elif payload.get('type') == 'flags':
                    table = Table(title="[yellow bold]🚩 Flags:[/yellow bold]")
                    table.add_column("Challenge Name", style="cyan")
                    table.add_column("Flag Value", style="green")
                    table.add_column("Points", style="red")
                    for flag in payload.get('content'):
                        table.add_row(flag[1], flag[2], str(flag[3]))
                    await self.new_msg(table)
            except Exception as e:
                await self.new_msg(f"\n❌ Error: {e}")
                await self.new_msg(f"Raw message: {data}")

    async def send_loop(self) -> None:
        """
        Asynchronous loop for getting user input,
        and sending websocket messages
        """
        with patch_stdout():
            while True:
                msg = await session.prompt_async(PROMPT)
                await self.parse(msg)

    async def new_msg(self, msg: str) -> None:
        """
        Adds a new message entry into chat history.
        Could be a user message or server broadcast

        Args:
            msg (string): Message to be added
        """
        await run_in_terminal(lambda: console.print(msg))

    async def parse(self, msg: str) -> None:
        """
        Parses a message by its type

        Args:
            msg (string): Raw string containing user input from prompt

        Returns:
            string: JSON string with added metadata for server
        """
        tokens: List[str] = msg.strip().split()
        payload: MessageData = {'from': USERNAME, 'timestamp': time.time()}

        if msg.startswith('/'):  # Command
            command = tokens[0][1:]  # Remove the leading '/'
            
            if command == 'quit':
                await self.stop()
                return

            if command in self.commands:
                await self.commands[command](tokens)
                return
            else:
                await self.new_msg(f"[red bold]ERROR:[/red bold] Unknown command: {command}")
                return
        else:  # Message
            payload['type'] = 'msg'
            payload['content'] = msg
            if self.websocket:
                await self.websocket.send(json.dumps(payload))

    async def cmd_flag(self, tokens: List[str]) -> None:
        """Handle flag submission"""
        if len(tokens) < 2:
            await self.new_msg("[red bold]ERROR:[/red bold] Invalid flag command format")
            await self.new_msg("Usage: /flag \\[submit|show] <challenge_name> <flag> <flag_value>")
            return

        action = tokens[1]

        if action == 'submit':
            if len(tokens) < 5:
                await self.new_msg("[red bold]ERROR:[/red bold] Invalid flag submission format")
                await self.new_msg("Usage: /flag submit <challenge_name> <flag> <flag_value>")
                return
                
            challenge_name = tokens[2]
            flag_value = tokens[3]
            flag_points = str(tokens[4])  # Convert to string to ensure proper rendering
            
            # Send flag submission to server
            data = {
                'type': 'flag',
                'from': self.username,
                'content': {
                    'action': 'submit',
                    'challenge_name': challenge_name,
                    'flag_value': flag_value,
                    'flag_points': flag_points,
                },
            }
        elif action == 'show':
            data = {
                'type': 'flag',
                'from': self.username,
                'content': {
                    'action': 'show',
                },
            }
        else:
            await self.new_msg("[red bold]ERROR:[/red bold] Invalid flag action")
            await self.new_msg("Usage: /flag \\[submit|show] <challenge_name> <flag> <flag_value>")
            return

        await self.websocket.send(json.dumps(data))