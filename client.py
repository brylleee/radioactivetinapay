#!/usr/bin/env python3

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
import ssl
from typing import Dict, Optional, Any, List, Union, Callable, Awaitable

PROMPT = ":(you)::> "
MULTILINE_PROMPT = ":(multiline)::> "
DEFAULT_PORT = 1107

USERNAME = os.environ.get('USER', os.environ.get('USERNAME'))

console = Console(soft_wrap=True, color_system="truecolor", file=sys.stdout)
session = PromptSession(style=Style.from_dict({
    'prompt': 'fg:yellow bold',
}))

user_colors: Dict[str, str] = {}

MessageData = Dict[str, Any]

class Client:
    def __init__(self, server_addr: str, conf: Dict[str, Any]) -> None:
        self.username = None
        self.server_addr = server_addr
        self.websocket = None
        self.multiline_mode = False
        self.multiline_buffer = []
        self.teamname = None
        self._password_session = None
        self._password_session = PromptSession(
            style=Style.from_dict({'prompt': 'fg:yellow bold'}),
            is_password=True
        )

        self.commands: Dict[str, Callable[[List[str]], Awaitable[None]]] = {
            'flag': self.cmd_flag,
            'multiline': self.cmd_multiline,
            'clear': self.cmd_clear,
            'help': self.cmd_help,
            'pm': self.cmd_pm,
            'team': self.cmd_team,
        }

        self.host: str = server_addr
        self.port: int = DEFAULT_PORT

        if ":" in server_addr:
            components = server_addr.split(":")
            self.host = components[0]
            self.port = int(components[1])

    async def run(self) -> None:
        await self.connect_server(self.host, self.port)
        await self.start()

    async def connect_server(self, host: str, port: int) -> None:
        payload: MessageData = {
            'type': 'auth',
            'from': USERNAME,
        }

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        with console.status("☢️ Connecting to Radioactive Tinapay server...", spinner="earth"):
            try:
                self.websocket = await asyncio.wait_for(
                    websockets.connect(f'wss://{host}:{port}', ssl=ssl_context),
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

        print("██████╗  █████╗ ██████╗        ████████╗██╗███╗   ██╗")
        print("██╔══██╗██╔══██╗██╔══██╗       ╚══██╔══╝██║████╗  ██║")
        print("██████╔╝███████║██║  ██║          ██║   ██║██╔██╗ ██║")
        print("██╔══██╗██╔══██║██║  ██║          ██║   ██║██║╚██╗██║")
        print("██║  ██║██║  ██║██████╔╝██╗       ██║   ██║██║ ╚████║██╗")
        print("╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚═╝       ╚═╝   ╚═╝╚═╝  ╚═══╝╚═╝")
        print("☢️ Radio-active!\n")

    async def start(self) -> None:
        with patch_stdout():
            try:
                await asyncio.gather(
                    self.send_loop(),
                    self.recv_loop()
                )
            except asyncio.CancelledError:
                pass

    async def stop(self) -> None:
        with console.status("☢️ Closing session", spinner="monkey"):
            if self.websocket:
                await self.websocket.close()

    async def recv_loop(self) -> None:
        while True:
            if not self.websocket:
                break
            data = await self.websocket.recv()
            try:
                payload: MessageData = json.loads(data)

                if payload.get('type') == 'team':
                    action = payload.get('action')
                    if action == 'create':
                        if payload.get('status') == 'pending':
                            await self.new_msg(f"[yellow bold]Team creation request for {payload.get('teamname')} pending server approval[/yellow bold]")
                        elif payload.get('status') == 'accepted':
                            self.teamname = payload.get('teamname')
                            await self.new_msg(f"[green bold]Team {payload.get('teamname')} created and authenticated[/green bold]")
                        elif payload.get('status') == 'rejected':
                            self.teamname = None
                            await self.new_msg(f"[red bold]Team creation failed: {payload.get('content')}[/red bold]")
                    elif action == 'join':
                        if payload.get('status') == 'accepted':
                            self.teamname = payload.get('teamname')
                            await self.new_msg(f"[green bold]Successfully joined team {payload.get('teamname')}[/green bold]")
                        elif payload.get('status') == 'rejected':
                            self.teamname = None
                            await self.new_msg(f"[red bold]Failed to join team: {payload.get('content')}[/red bold]")
                    elif action == 'kick':
                        if payload.get('status') == 'accepted':
                            self.teamname = None
                            await self.new_msg(f"[red bold]{payload.get('content')}[/red bold]")
                        elif payload.get('status') == 'rejected':
                            await self.new_msg(f"[red bold]Kick failed: {payload.get('content')}[/red bold]")
                    elif action == 'dq':
                        self.teamname = None
                        await self.new_msg(f"[red bold]{payload.get('content')}[/red bold]")
                        await self.stop()
                    elif action == 'list':
                        table = Table(title="[yellow bold]Teams[/yellow bold]")
                        table.add_column("Team Name", style="cyan")
                        table.add_column("Leader", style="yellow")
                        table.add_column("Members", style="green")
                        for team in payload.get('content'):
                            members = ", ".join(team['members'])
                            table.add_row(team['teamname'], team['leader'], members)
                        await self.new_msg(table)
                    elif action == 'listteams':
                        table = Table(title="[yellow bold]Available Teams[/yellow bold]")
                        table.add_column("Team Name", style="cyan")
                        for teamname in payload.get('content'):
                            table.add_row(teamname)
                        await self.new_msg(table)

                elif payload.get('type') == 'msg':
                    if payload.get('from') not in user_colors.keys():
                        user_colors[payload.get('from')] = '#' + ''.join(choices('4321abcdef', k=6))
                    prefix = f"[Breakroom {self.teamname}] " if payload.get('breakroom') and self.teamname else ""
                    await self.new_msg(f"{prefix}<[{user_colors[payload.get('from')]}]{payload.get('from')}[/]> {payload.get('content')}")
                elif payload.get('type') == 'pm':
                    if payload.get('from') not in user_colors.keys():
                        user_colors[payload.get('from')] = '#' + ''.join(choices('4321abcdef', k=6))
                    prefix = f"[Breakroom {self.teamname}] " if payload.get('breakroom') and self.teamname else ""
                    await self.new_msg(f"{prefix}[cyan]PM from <[{user_colors[payload.get('from')]}]{payload.get('from')}[/]>: {payload.get('content')}")
                elif payload.get('type') == 'announce':
                    await self.new_msg(f"[yellow bold]📢 SERVER:[/yellow bold] {payload.get('content')}")
                elif payload.get('type') == 'auth':
                    if payload.get('status') == 'pending':
                        await self.new_msg(f"[yellow bold]⚠️ Auth request from {payload.get('from')}[/yellow bold]")
                        await self.new_msg(f"[yellow]Use /auth accept {payload.get('from')} to accept[/yellow]")
                        await self.new_msg(f"[yellow]Use /auth reject {payload.get('from')} to reject[/yellow]")
                    elif payload.get('status') == 'accepted':
                        await self.new_msg(f"[green bold]✅ Connection accepted by server[/green bold]")
                    elif payload.get('status') == 'rejected':
                        await self.new_msg(f"[red bold]❌ Connection rejected by server[/red bold]")
                        await self.stop()
                elif payload.get('type') == 'flags':
                    table = Table(title="[yellow bold]🚩 Flags:[/yellow bold]")
                    table.add_column("Challenge Name", style="cyan")
                    table.add_column("Flag Value", style="green")
                    table.add_column("Points", style="red")
                    table.add_column("Team", style="yellow")
                    for flag in payload.get('content'):
                        table.add_row(flag[1], flag[2], str(flag[3]), flag[4] or "None")
                    await self.new_msg(table)
                elif payload.get('type') == 'flag':
                    if payload.get('status') == 'accepted':
                        await self.new_msg(f"[green bold]{payload.get('content')}[/green bold]")
                    elif payload.get('status') == 'rejected':
                        await self.new_msg(f"[red bold]Flag submission failed: {payload.get('content')}[/red bold]")
            except Exception as e:
                await self.new_msg(f"\n❌ Error: {e}")
                await self.new_msg(f"Raw message: {data}")

    async def send_loop(self) -> None:
        with patch_stdout():
            while True:
                prompt = MULTILINE_PROMPT if self.multiline_mode else PROMPT
                msg = await session.prompt_async(prompt)
                if self.multiline_mode:
                    if msg.strip().upper() == "END":
                        message = "\n".join(self.multiline_buffer)
                        if message:
                            payload = {
                                'type': 'msg',
                                'from': self.username,
                                'content': message,
                                'timestamp': time.time(),
                                'breakroom': '--breakroom' in message
                            }
                            if '--breakroom' in message:
                                message = message.replace('--breakroom', '').strip()
                                payload['content'] = message
                            if self.websocket:
                                await self.websocket.send(json.dumps(payload))
                                await self.new_msg(f"[green]Sent multiline message:[/green] {message}")
                        self.multiline_mode = False
                        self.multiline_buffer = []
                        await self.new_msg("[yellow bold]Multiline mode off[/yellow bold]")
                    else:
                        self.multiline_buffer.append(msg)
                else:
                    await self.parse(msg)

    async def new_msg(self, msg: str) -> None:
        await run_in_terminal(lambda: console.print(msg))

    async def parse(self, msg: str) -> None:
        tokens: List[str] = msg.strip().split()
        payload: MessageData = {'from': self.username, 'timestamp': time.time()}

        if msg.startswith('/'):
            command = tokens[0][1:]
            if command == 'quit':
                await self.stop()
                return
            if command in self.commands:
                await self.commands[command](tokens)
                return
            else:
                await self.new_msg(f"[red bold]ERROR:[/red bold] Unknown command: {command}")
                return
        else:
            payload['type'] = 'msg'
            payload['content'] = msg.replace('--breakroom', '').strip()
            payload['breakroom'] = '--breakroom' in msg
            if not payload['content']:
                return
            if payload['breakroom'] and not self.teamname:
                await self.new_msg("[red bold]ERROR:[/red bold] You must be in a team to send breakroom messages")
                return
            if self.websocket:
                await self.websocket.send(json.dumps(payload))

    async def cmd_flag(self, tokens: List[str]) -> None:
        if len(tokens) < 2:
            await self.new_msg("[red bold]ERROR:[/red bold] Invalid flag command format")
            await self.new_msg("Usage: /flag \\[submit|show] <challenge_name> <flag> <points> [--breakroom]")
            return

        action = tokens[1]
        breakroom = '--breakroom' in tokens
        if breakroom:
            if not self.teamname:
                await self.new_msg("[red bold]ERROR:[/red bold] You must be in a team to use breakroom flags")
                return
            tokens = [t for t in tokens if t != '--breakroom']

        if action == 'submit':
            if len(tokens) < 5:
                await self.new_msg("[red bold]ERROR:[/red bold] Invalid flag submission format")
                await self.new_msg("Usage: /flag submit <challenge_name> <flag> <points> [--breakroom]")
                return

            challenge_name = tokens[2]
            flag_value = tokens[3]
            try:
                flag_points = int(tokens[4])
            except ValueError:
                await self.new_msg("[red bold]ERROR:[/red bold] Points must be a valid integer")
                return

            data = {
                'type': 'flag',
                'from': self.username,
                'content': {
                    'action': 'submit',
                    'challenge_name': challenge_name,
                    'flag_value': flag_value,
                    'flag_points': flag_points,
                },
                'breakroom': breakroom,
                'timestamp': time.time()
            }
            if self.websocket:
                await self.websocket.send(json.dumps(data))
        elif action == 'show':
            data = {
                'type': 'flag',
                'from': self.username,
                'content': {
                    'action': 'show',
                },
                'breakroom': breakroom,
                'timestamp': time.time()
            }
            if self.websocket:
                await self.websocket.send(json.dumps(data))
        else:
            await self.new_msg("[red bold]ERROR:[/red bold] Invalid flag action")
            await self.new_msg("Usage: /flag \\[submit|show] <challenge_name> <flag> <points> [--breakroom]")

    async def cmd_multiline(self, tokens: List[str]) -> None:
        if self.multiline_mode:
            await self.new_msg("[yellow bold]Already in multiline mode. Type 'END' to send.[/yellow bold]")
        else:
            self.multiline_mode = True
            self.multiline_buffer = []
            await self.new_msg("[yellow bold]Multiline mode on. Type 'END' to send the message.[/yellow bold]")

    async def cmd_clear(self, tokens: List[str]) -> None:
        console.clear()
        await self.new_msg("[green bold]INFO:[/green bold] Terminal cleared")

    async def cmd_help(self, tokens: List[str]) -> None:
        table = Table(title="[yellow bold]Client Commands[/yellow bold]")
        table.add_column("Command", style="cyan")
        table.add_column("Usage", style="green")

        commands = [
            ("/flag", "/flag [submit|show] <challenge_name> <flag> <points> [--breakroom] - Submit or display flags"),
            ("/team", "/team [create|join|list|listteams|kick] ... - Manage teams"),
            ("/multiline", "/multiline - Enter multiline mode (type 'END' to send)"),
            ("/clear", "/clear - Clear the terminal screen"),
            ("/help", "/help - Display this help message"),
            ("/quit", "/quit - Disconnect from the server"),
            ("/pm", "/pm <username> <message> [--breakroom] - Send a private message to a user")
        ]

        for cmd, usage in commands:
            table.add_row(cmd, usage)

        await self.new_msg(table)

    async def cmd_pm(self, tokens: List[str]) -> None:
        if len(tokens) < 3:
            await self.new_msg("[red bold]ERROR:[/red bold] Invalid private message format")
            await self.new_msg("Usage: /pm <username> <message> [--breakroom]")
            return

        breakroom = '--breakroom' in tokens
        if breakroom:
            if not self.teamname:
                await self.new_msg("[red bold]ERROR:[/red bold] You must be in a team to send breakroom private messages")
                return
            tokens = [t for t in tokens if t != '--breakroom']

        target_user = tokens[1]
        message = " ".join(tokens[2:])

        if not message:
            await self.new_msg("[red bold]ERROR:[/red bold] Message cannot be empty")
            return

        if target_user == self.username:
            await self.new_msg("[red bold]ERROR:[/red bold] Cannot send private message to yourself")
            return

        payload = {
            'type': 'pm',
            'from': self.username,
            'to': target_user,
            'content': message,
            'timestamp': time.time(),
            'breakroom': breakroom
        }
        if self.websocket:
            await self.websocket.send(json.dumps(payload))
            await self.new_msg(f"[cyan]PM to {target_user}:[/cyan] {message}")
        else:
            await self.new_msg("[red bold]ERROR:[/red bold] Not connected to server")

    async def cmd_team(self, tokens: List[str]) -> None:
        if len(tokens) < 2:
            await self.new_msg("[red bold]ERROR:[/red bold] Invalid team command format")
            await self.new_msg("Usage: /team \\[create|join|list|listteams|kick] <teamname>")
            return

        action = tokens[1]
        
        if self._password_session is None:
            self._password_session = PromptSession(
                style=Style.from_dict({'prompt': 'fg:yellow bold'}),
                is_password=True
            )
            await self.new_msg("[yellow bold]DEBUG:[/yellow bold] Initialized new password session")

        if action == 'create':
            if len(tokens) < 3:
                await self.new_msg("[red bold]ERROR:[/red bold] Missing teamname")
                await self.new_msg("Usage: /team create <teamname>")
                return
            teamname = tokens[2]
            password = await self._password_session.prompt_async(
                [("class:prompt", "Enter team password: ")]
            )

            if not password:
                await self.new_msg("[red bold]ERROR:[/red bold] Password cannot be empty")
                return
            payload = {
                'type': 'team',
                'action': 'create',
                'from': self.username,
                'teamname': teamname,
                'password': password,
                'timestamp': time.time()
            }
            await self.websocket.send(json.dumps(payload))

        elif action == 'join':
            if len(tokens) < 3:
                await self.new_msg("[red bold]ERROR:[/red bold] Missing teamname")
                await self.new_msg("Usage: /team join <teamname>")
                return
            teamname = tokens[2]
            await self.new_msg("[yellow bold]DEBUG:[/yellow bold] Starting team join for " + teamname)
            password = await self._password_session.prompt_async(
                [("class:prompt", "Enter team password: ")]
            )

            if not password:
                await self.new_msg("[red bold]ERROR:[/red bold] Password cannot be empty")
                return
            payload = {
                'type': 'team',
                'action': 'join',
                'from': self.username,
                'teamname': teamname,
                'password': password,
                'timestamp': time.time()
            }
            await self.websocket.send(json.dumps(payload))
            await self.new_msg("[yellow bold]DEBUG:[/yellow bold] Team join request sent for " + teamname)

        elif action == 'list':
            payload = {
                'type': 'team',
                'action': 'list',
                'from': self.username,
                'timestamp': time.time()
            }
            await self.websocket.send(json.dumps(payload))

        elif action == 'listteams':
            payload = {
                'type': 'team',
                'action': 'listteams',
                'from': self.username,
                'timestamp': time.time()
            }
            await self.websocket.send(json.dumps(payload))

        elif action == 'kick':
            if len(tokens) < 3:
                await self.new_msg("[red bold]ERROR:[/red bold] Missing username")
                await self.new_msg("Usage: /team kick <username>")
                return
            if not self.teamname:
                await self.new_msg("[red bold]ERROR:[/red bold] You must be in a team to kick members")
                return
            target_user = tokens[2]
            payload = {
                'type': 'team',
                'action': 'kick',
                'from': self.username,
                'teamname': self.teamname,
                'target_user': target_user,
                'timestamp': time.time()
            }
            await self.websocket.send(json.dumps(payload))

        else:
            await self.new_msg("[red bold]ERROR:[/red bold] Invalid team action")
            await self.new_msg("Usage: /team [create|join|list|listteams|kick] ...")
