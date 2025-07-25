#!/usr/bin/env python3

from rich.console import Console
from rich.theme import Theme
from rich.table import Table

from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.application import run_in_terminal

from typing import Dict, Optional, Any, Union, Callable, Awaitable, List

import websockets
import asyncio
import sqlite3
import time
import json
import sys
import ssl
import os
from datetime import datetime
from websockets.server import WebSocketServerProtocol

DEFAULT_PORT = 1107
PROMPT = "(server)> "
MULTILINE_PROMPT = ":(multiline)::> "

console = Console(soft_wrap=True, color_system="truecolor", file=sys.stdout)
session = PromptSession(style=Style.from_dict({
    'prompt': 'fg:red bold',
}))

MessageData = Dict[str, Any]

class Server:
    def __init__(self, server_addr: str, conf: Dict[str, Any]) -> None:
        # Initialize session information
        self.session_name = self._get_session_name()
        self.session_details = self._get_session_details()
        self.max_users = self._get_max_users()
        self.debug_mode = self._get_debug_mode()
        self.session_start = datetime.now()

        # Multiline mode state
        self.multiline_mode = False
        self.multiline_buffer = []

        # Create database file with session timestamp
        self.db_filename = f"session_{self.session_start.strftime('%Y%m%d_%H%M%S')}.db"
        self._create_database()

        # Stores client connections and teams
        self.connections: Dict[str, WebSocketServerProtocol] = {}
        self.wait_list: Dict[str, WebSocketServerProtocol] = {}
        self.teams: Dict[str, Dict[str, Any]] = {}  # {teamname: {password: str, members: List[str], leader: str}}
        self.pending_teams: Dict[str, Dict[str, Any]] = {}  # {teamname: {password: str, leader: str}}
        self.user_to_team: Dict[str, str] = {}  # {username: teamname}

        self.server: Optional[websockets.WebSocketServer] = None

        # Store message methods
        self.handlers: Dict[str, Callable[[MessageData, WebSocketServerProtocol], Awaitable[None]]] = {
            'msg': self.send_msg,
            'auth': self.authenticate,
            'flag': self.handle_flag,
            'pm': self.handle_pm,
            'team': self.handle_team,
        }

        # Store command methods
        self.commands: Dict[str, Callable[[List[str]], Awaitable[None]]] = {
            'auth': self.cmd_auth,
            'users': self.cmd_users,
            'flag': self.cmd_flag,
            'help': self.cmd_help,
            'clear': self.cmd_clear,
            'multiline': self.cmd_multiline,
            'pm': self.cmd_pm,
            'team': self.cmd_team,
        }

        self.host: str = server_addr
        self.port: int = DEFAULT_PORT

        if ":" in server_addr:
            components = server_addr.split(":")
            self.host = components[0]
            self.port = int(components[1])

        console.log("☢️ Starting Radioactive Tinapay server\n")

    def _get_session_name(self) -> str:
        while True:
            name = input("Enter session name: ").strip()
            if name:
                return name
            console.print("[red]Session name cannot be empty![/red]")

    def _get_session_details(self) -> str:
        while True:
            details = input("Enter session details: ").strip()
            if details:
                return details
            console.print("[red]Session details cannot be empty![/red]")

    def _get_max_users(self) -> int:
        while True:
            try:
                max_users = int(input("Enter maximum number of users: ").strip())
                if max_users > 0:
                    return max_users
                console.print("[red]Maximum users must be greater than 0![/red]")
            except ValueError:
                console.print("[red]Please enter a valid number![/red]")

    def _get_debug_mode(self) -> bool:
        while True:
            response = input("Enable debug mode? (y/N): ").strip().lower()
            if response in ['y', 'n', '']:
                return response == 'y'
            console.print("[red]Please enter 'y' or 'N' (or press Enter for 'N')![/red]")

    def _create_database(self) -> None:
        conn = sqlite3.connect(self.db_filename)
        cursor = conn.cursor()

        # Create session_details table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS session_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            details TEXT NOT NULL,
            max_users INTEGER NOT NULL,
            start_time TIMESTAMP NOT NULL
        )
        ''')

        # Create flag table with challenge_name, flag_value, points, and teamname
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS flag (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenge_name TEXT NOT NULL,
            flag_value TEXT NOT NULL,
            points INTEGER NOT NULL,
            teamname TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # Insert session details
        cursor.execute('''
        INSERT INTO session_details (name, details, max_users, start_time)
        VALUES (?, ?, ?, ?)
        ''', (self.session_name, self.session_details, self.max_users, self.session_start))

        conn.commit()
        conn.close()

        console.print(f"[green]Created database file: {self.db_filename}[/green]")

    async def start(self) -> None:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert_file = "server.crt"
        key_file = "server.key"

        if not os.path.exists(cert_file) or not os.path.exists(key_file):
            console.print("[red bold]ERROR:[/red bold] SSL certificate or key file not found.")
            sys.exit(1)

        try:
            ssl_context.load_cert_chain(certfile=cert_file, keyfile=key_file)
        except Exception as e:
            console.print(f"[red bold]ERROR:[/red bold] Failed to load SSL certificate: {e}")
            sys.exit(1)

        self.server = await websockets.serve(self.handler, self.host, self.port, ssl=ssl_context)
        print(open(".radioactive", "r").read().replace('\\x1b', '\x1b').replace('\\n', '\n'))
        await self.new_log(f"Radioactive Tinapay running at: [magenta bold]wss://{self.host}:{self.port}[/magenta bold]\n")

        with patch_stdout():
            await asyncio.gather(
                self.server.wait_closed(),
                self.command_loop()
            )

    async def cmd_stop(self) -> None:
        for connection in self.connections:
            await self.send_announcement(f"💀 Server is shutting down")
            await self.connections[connection].close()

        for connection in self.wait_list:
            await self.send_announcement(f"💀 Server is shutting down")
            await self.wait_list[connection].close()

        if self.server:
            self.server.close()
            await self.server.wait_closed()

        sys.exit(0)

    async def handler(self, websocket: WebSocketServerProtocol) -> None:
        while True:
            try:
                data = await websocket.recv()
                if self.debug_mode:
                    await self.new_log(f"[yellow bold]DEBUG:[/yellow bold] Received WebSocket message: {data}")
            except (websockets.exceptions.ConnectionClosedOK, websockets.exceptions.ConnectionClosedError):
                for client in list(self.connections.keys()):
                    if self.connections.get(client) == websocket:
                        await self.new_log(f"[green bold]INFO:[/green bold] Received disconnect from user: `{client}`")
                        await self.send_announcement(f"User `{client}` has disconnected")
                        if client in self.connections:
                            teamname = self.user_to_team.get(client)
                            if teamname and teamname in self.teams:
                                self.teams[teamname]['members'].remove(client)
                                if not self.teams[teamname]['members']:
                                    del self.teams[teamname]
                                await self.send_team_message(teamname, f"User `{client}` has left the team")
                            del self.user_to_team[client]
                            del self.connections[client]
                        break

                for client in list(self.wait_list.keys()):
                    if self.wait_list.get(client) == websocket:
                        await self.new_log(f"[green bold]INFO:[/green bold] Received disconnect from waitlist user `{client}`")
                        await self.send_announcement(f"Waitlist user `{client}` has disconnected")
                        if client in self.wait_list:
                            del self.wait_list[client]
                        break
                return

            try:
                data = json.loads(data)
            except json.JSONDecodeError as e:
                await self.new_log(f"[red bold]ERROR:[/red bold] Invalid JSON: {e}")
                continue

            if data.get('from') not in self.connections.keys() and data.get('type') not in ['auth', 'team']:
                await self.new_log(f"[red bold]ERROR:[/red bold] Unauthenticated client: {data.get('from')}")
                return

            if data.get('type') == 'msg' and data.get('to') == None:
                teamname = self.user_to_team.get(data.get('from'))
                if data.get('breakroom') and teamname:
                    await self.new_log(f"Breakroom message from `{data.get('from')}` in `{teamname}`: {data.get('content')}")
                    await self.send_team_message(teamname, f"[{data.get('from')}] {data.get('content')}")
                else:
                    await self.new_log(f"Message from `{data.get('from')}`: {data.get('content')}")
                    await self.send_msg(data)
            elif data.get('type') in self.handlers:
                await self.handlers[data.get('type')](data, websocket)
            else:
                await self.new_log(f"[red bold]ERROR:[/red bold] Unknown message type: {data.get('type')}")

    async def new_log(self, log: str) -> None:
        if '[yellow bold]DEBUG:[/yellow bold]' in log and not self.debug_mode:
            return
        await run_in_terminal(lambda: console.log(log))

    async def cmd_auth(self, tokens: List[str]) -> None:
        if len(tokens) < 2:
            await self.new_log("[red bold]ERROR:[/red bold] Invalid auth command format")
            await self.new_log("Usage: /auth \\[accept|reject|list] <username>")
            return

        action = tokens[1]

        if action == 'list':
            table = Table(title="Waitlist Users")
            table.add_column("Username", style="cyan")
            table.add_column("Status", style="yellow")

            if not self.wait_list:
                table.add_row("No users", "Empty waitlist")
            else:
                for username in self.wait_list:
                    table.add_row(username, "Pending")

            console.print(table)
            return

        if len(tokens) < 3:
            await self.new_log("[red bold]ERROR:[/red bold] Missing username")
            return

        client = tokens[2]

        if action == 'accept':
            if client in self.wait_list:
                await self.new_log(f"[green bold]INFO:[/green bold] Accepting connection from {client}")
                self.connections[client] = self.wait_list[client]
                response: MessageData = {
                    'type': 'auth',
                    'status': 'accepted',
                    'from': client
                }
                await self.wait_list[client].send(json.dumps(response))
                await self.send_announcement(f"User `{client}` has joined")
                del self.wait_list[client]
            else:
                await self.new_log(f"[red bold]ERROR:[/red bold] Client `{client}` not in wait list")
        elif action == 'reject':
            if client in self.wait_list:
                response: MessageData = {
                    'type': 'auth',
                    'status': 'rejected',
                    'from': client
                }
                await self.wait_list[client].send(json.dumps(response))
                await self.wait_list[client].close()
                del self.wait_list[client]
            else:
                await self.new_log(f"[red bold]ERROR:[/red bold] Client `{client}` not in wait list")

    async def cmd_users(self, tokens: List[str]) -> None:
        if len(tokens) < 2:
            await self.new_log("[red bold]ERROR:[/red bold] Invalid users command format")
            await self.new_log("Usage: /users \\[list|kick] <username>")
            return

        action = tokens[1]

        if action == 'list':
            table = Table(title="Connected Users")
            table.add_column("Username", style="cyan")
            table.add_column("Team", style="yellow")
            table.add_column("Status", style="green")

            if not self.connections:
                table.add_row("No users", "N/A", "No connections")
            else:
                for username in self.connections:
                    teamname = self.user_to_team.get(username, "None")
                    table.add_row(username, teamname, "Connected")

            console.print(table)
            return

        if action == 'kick':
            if len(tokens) < 3:
                await self.new_log("[red bold]ERROR:[/red bold] Missing username")
                return

            client = tokens[2]
            if client in self.connections:
                await self.new_log(f"[green bold]INFO:[/green bold] Kicking user: `{client}`")
                await self.send_announcement(f"User `{client}` has been kicked")
                teamname = self.user_to_team.get(client)
                if teamname and teamname in self.teams:
                    self.teams[teamname]['members'].remove(client)
                    if not self.teams[teamname]['members']:
                        del self.teams[teamname]
                    await self.send_team_message(teamname, f"User `{client}` has been kicked")
                del self.user_to_team[client]
                await self.connections[client].close()
                del self.connections[client]
            else:
                await self.new_log(f"[red bold]ERROR:[/red bold] User `{client}` not found")

    async def cmd_flag(self, tokens: List[str]) -> None:
        if len(tokens) < 2:
            await self.new_log("[red bold]ERROR:[/red bold] Invalid flag command format")
            await self.new_log("Usage: /flag \\[submit|show] <challenge_name> <flag_value> <points>")
            return

        action = tokens[1]

        if action == 'submit':
            if len(tokens) < 5:
                await self.new_log("[red bold]ERROR:[/red bold] Invalid flag submission format")
                await self.new_log("Usage: /flag submit <challenge_name> <flag_value> <points>")
                return

            challenge_name = tokens[2]
            flag_value = tokens[3]
            try:
                points = int(tokens[4])
            except ValueError:
                await self.new_log("[red bold]ERROR:[/red bold] Points must be a valid integer")
                return

            conn = sqlite3.connect(self.db_filename)
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT INTO flag (challenge_name, flag_value, points) VALUES (?, ?, ?)",
                    (challenge_name, flag_value, points)
                )
                conn.commit()
                await self.new_log(
                    f"[green bold]INFO:[/green bold] Flag submitted: {challenge_name} - {flag_value} - {points} points"
                )
                await self.send_announcement(
                    f"🚩 [green bold]Flag submitted:[/green bold] {challenge_name} - {flag_value} - {points} points"
                )
            except sqlite3.Error as e:
                await self.new_log(f"[red bold]ERROR:[/red bold] Database error during flag submission: {e}")
            finally:
                conn.close()

        elif action == 'show':
            conn = sqlite3.connect(self.db_filename)
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT * FROM flag")
                flag_list = cursor.fetchall()
                table = Table(title="[yellow bold]🚩 Flags:[/yellow bold]")
                table.add_column("Challenge Name", style="cyan")
                table.add_column("Flag Value", style="green")
                table.add_column("Points", style="red")
                table.add_column("Team", style="yellow")

                if not flag_list:
                    table.add_row("No flags", "Empty", "0", "N/A")
                else:
                    for flag in flag_list:
                        table.add_row(flag[1], flag[2], str(flag[3]), flag[4] or "None")

                console.print(table)
            except sqlite3.Error as e:
                await self.new_log(f"[red bold]ERROR:[/red bold] Database error during flag retrieval: {e}")
            finally:
                conn.close()

    async def cmd_team(self, tokens: List[str]) -> None:
        if len(tokens) < 2:
            await self.new_log("[red bold]ERROR:[/red bold] Invalid team command format")
            await self.new_log("Usage: /team \\[auth|dq|pending|list|flags] <teamname>")
            return

        action = tokens[1]

        if action == 'auth':
            if len(tokens) < 3:
                await self.new_log("[red bold]ERROR:[/red bold] Missing teamname")
                await self.new_log("Usage: /team auth <teamname>")
                return
            teamname = tokens[2]
            if teamname in self.pending_teams:
                await self.new_log(f"[green bold]INFO:[/green bold] Accepting team `{teamname}`")
                self.teams[teamname] = {
                    'password': self.pending_teams[teamname]['password'],
                    'members': [self.pending_teams[teamname]['leader']],
                    'leader': self.pending_teams[teamname]['leader']
                }
                leader = self.pending_teams[teamname]['leader']
                self.user_to_team[leader] = teamname
                response = {
                    'type': 'team',
                    'action': 'create',
                    'status': 'accepted',
                    'teamname': teamname
                }
                await self.connections[leader].send(json.dumps(response))
                await self.send_announcement(f"Team `{teamname}` has been authenticated")
                del self.pending_teams[teamname]
            else:
                await self.new_log(f"[red bold]ERROR:[/red bold] Team `{teamname}` not in pending list")

        elif action == 'dq':
            if len(tokens) < 3:
                await self.new_log("[red bold]ERROR:[/red bold] Missing teamname or username")
                await self.new_log("Usage: /team dq <teamname|username>")
                return
            target = tokens[2]
            if target in self.teams:
                teamname = target
                await self.new_log(f"[green bold]INFO:[/green bold] Disqualifying team `{teamname}`")
                for member in self.teams[teamname]['members']:
                    if member in self.connections:
                        await self.connections[member].send(json.dumps({
                            'type': 'team',
                            'action': 'dq',
                            'teamname': teamname,
                            'content': f"Team `{teamname}` has been disqualified"
                        }))
                        await self.connections[member].close()
                        del self.connections[member]
                    del self.user_to_team[member]
                del self.teams[teamname]
                await self.send_announcement(f"Team `{teamname}` has been disqualified")
            elif target in self.user_to_team:
                teamname = self.user_to_team[target]
                await self.new_log(f"[green bold]INFO:[/green bold] Disqualifying user `{target}` from team `{teamname}`")
                self.teams[teamname]['members'].remove(target)
                if not self.teams[teamname]['members']:
                    del self.teams[teamname]
                else:
                    await self.send_team_message(teamname, f"User `{target}` has been disqualified")
                if target in self.connections:
                    await self.connections[target].send(json.dumps({
                        'type': 'team',
                        'action': 'dq',
                        'teamname': teamname,
                        'content': f"You have been disqualified from team `{teamname}`"
                    }))
                    await self.connections[target].close()
                    del self.connections[target]
                del self.user_to_team[target]
            else:
                await self.new_log(f"[red bold]ERROR:[/red bold] Team or user `{target}` not found")

        elif action == 'pending':
            table = Table(title="Pending Teams")
            table.add_column("Team Name", style="cyan")
            table.add_column("Leader", style="yellow")
            if not self.pending_teams:
                table.add_row("No teams", "Empty")
            else:
                for teamname, info in self.pending_teams.items():
                    table.add_row(teamname, info['leader'])
            console.print(table)

        elif action == 'list':
            table = Table(title="Authenticated Teams")
            table.add_column("Team Name", style="cyan")
            table.add_column("Leader", style="yellow")
            table.add_column("Members", style="green")
            if not self.teams:
                table.add_row("No teams", "N/A", "Empty")
            else:
                for teamname, info in self.teams.items():
                    members = ", ".join(info['members'])
                    table.add_row(teamname, info['leader'], members)
            console.print(table)

        elif action == 'flags':
            conn = sqlite3.connect(self.db_filename)
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT * FROM flag WHERE teamname IS NOT NULL")
                flag_list = cursor.fetchall()
                table = Table(title="[yellow bold]🚩 Team Flags:[/yellow bold]")
                table.add_column("Team Name", style="yellow")
                table.add_column("Challenge Name", style="cyan")
                table.add_column("Flag Value", style="green")
                table.add_column("Points", style="red")
                if not flag_list:
                    table.add_row("No flags", "Empty", "N/A", "0")
                else:
                    for flag in flag_list:
                        table.add_row(flag[4], flag[1], flag[2], str(flag[3]))
                console.print(table)
            except sqlite3.Error as e:
                await self.new_log(f"[red bold]ERROR:[/red bold] Database error during flag retrieval: {e}")
            finally:
                conn.close()

        else:
            await self.new_log("[red bold]ERROR:[/red bold] Invalid team action")
            await self.new_log("Usage: /team [auth|dq|pending|list|flags] ...")

    async def cmd_help(self, tokens: List[str]) -> None:
        table = Table(title="[yellow bold]Server Commands[/yellow bold]")
        table.add_column("Command", style="cyan")
        table.add_column("Usage", style="green")

        commands = [
            ("/auth", "/auth [accept|reject|list] <username> - Manage client authentication"),
            ("/users", "/users [list|kick] <username> - Manage connected users"),
            ("/flag", "/flag [submit|show] <challenge_name> <flag_value> <points> - Submit or display flags"),
            ("/team", "/team [auth|dq|pending|list|flags] <teamname> - Manage teams"),
            ("/multiline", "/multiline - Enter multiline mode (type 'END' to send)"),
            ("/help", "/help - Display this help message"),
            ("/clear", "/clear - Clear the terminal screen"),
            ("/stop", "/stop - Shut down the server"),
            ("/pm", "/pm <username> <message> - Send a private message to a user")
        ]

        for cmd, usage in commands:
            table.add_row(cmd, usage)

        console.print(table)

    async def cmd_clear(self, tokens: List[str]) -> None:
        console.clear()
        await self.new_log("[green bold]INFO:[/green bold] Terminal cleared")

    async def cmd_multiline(self, tokens: List[str]) -> None:
        if self.multiline_mode:
            await self.new_log("[yellow bold]Already in multiline mode. Type 'END' to send.[/yellow bold]")
        else:
            self.multiline_mode = True
            self.multiline_buffer = []
            await self.new_log("[yellow bold]Multiline mode on. Type 'END' to send the message.[/yellow bold]")

    async def cmd_pm(self, tokens: List[str]) -> None:
        if len(tokens) < 3:
            await self.new_log("[red bold]ERROR:[/red bold] Invalid private message format")
            await self.new_log("Usage: /pm <username> <message>")
            return

        target_user = tokens[1]
        message = " ".join(tokens[2:])

        if not message:
            await self.new_log("[red bold]ERROR:[/red bold] Message cannot be empty")
            return

        if target_user not in self.connections:
            await self.new_log(f"[red bold]ERROR:[/red bold] User `{target_user}` not found")
            return

        payload = {
            'type': 'pm',
            'from': 'server',
            'to': target_user,
            'content': message,
            'timestamp': time.time()
        }
        try:
            await self.connections[target_user].send(json.dumps(payload))
            await self.new_log(f"[green bold]INFO:[/green bold] Private message sent to `{target_user}`: {message}")
        except websockets.exceptions.ConnectionClosed:
            await self.new_log(f"[yellow bold]NOTICE:[/yellow bold] Couldn't reach {target_user}")

    async def send_server_message(self, message: str) -> None:
        payload: MessageData = {
            'type': 'msg',
            'from': 'server',
            'content': message,
            'timestamp': time.time()
        }
        await self.new_log(f"Message from `server`: {message}")
        await self.send_msg(payload)

    async def command_loop(self) -> None:
        with patch_stdout():
            while True:
                prompt = MULTILINE_PROMPT if self.multiline_mode else PROMPT
                input_str = await session.prompt_async(prompt)
                if self.multiline_mode:
                    if input_str.strip().upper() == "END":
                        message = "\n".join(self.multiline_buffer)
                        if message:
                            await self.send_server_message(message)
                            await self.new_log(f"[green]Sent multiline message:[/green] {message}")
                        self.multiline_mode = False
                        self.multiline_buffer = []
                        await self.new_log("[yellow bold]Multiline mode off[/yellow bold]")
                    else:
                        self.multiline_buffer.append(input_str)
                else:
                    tokens = input_str.strip().split()
                    if not tokens:
                        continue

                    if input_str.startswith('/'):
                        command = tokens[0][1:]
                        if command == 'stop':
                            await self.cmd_stop()
                            return
                        if command in self.commands:
                            await self.commands[command](tokens)
                        else:
                            await self.new_log(f"[red bold]ERROR:[/red bold] Unknown command: {command}")
                    else:
                        message = input_str.strip()
                        if message:
                            await self.send_server_message(message)

    async def authenticate(self, data: MessageData, websocket: WebSocketServerProtocol) -> None:
        try:
            if data.get('from') not in self.connections:
                self.wait_list[data.get('from')] = websocket
                await self.new_log(f"[green bold]INFO:[/green bold] ⌛ Auth request from `{data.get('from')}`")
                response: MessageData = {
                    'type': 'auth',
                    'status': 'pending',
                    'from': data.get('from')
                }
                await websocket.send(json.dumps(response))
        except Exception as e:
            await self.new_log(f"⚠️ [red]Failed authentication attempt: {e}[/red]")
            response: MessageData = {
                'type': 'auth',
                'status': 'rejected',
                'from': data.get('from')
            }
            await websocket.send(json.dumps(response))

    async def send_msg(self, data: MessageData, to: str = 'broadcast') -> None:
        data['timestamp'] = time.time()
        sender = data['from']
        data = json.dumps(data)

        for client_name, client in self.connections.items():
            if client_name != sender or sender == 'server':
                try:
                    await client.send(data)
                except websockets.exceptions.ConnectionClosed:
                    console.log(f"[yellow bold][=] NOTICE:[/yellow bold] Couldn't reach {client_name}")

    async def send_team_message(self, teamname: str, message: str) -> None:
        if teamname not in self.teams:
            return
        payload = {
            'type': 'msg',
            'from': 'server',
            'content': f"[Breakroom {teamname}] {message}",
            'timestamp': time.time(),
            'breakroom': True
        }
        data = json.dumps(payload)
        for member in self.teams[teamname]['members']:
            if member in self.connections:
                try:
                    await self.connections[member].send(data)
                except websockets.exceptions.ConnectionClosed:
                    continue

    async def send_announcement(self, message: str) -> None:
        payload: MessageData = {
            'type': 'announce',
            'content': message,
            'timestamp': time.time()
        }
        data = json.dumps(payload)

        for client in self.connections.values():
            try:
                await client.send(data)
            except websockets.exceptions.ConnectionClosed:
                continue

    async def handle_flag(self, data: MessageData, websocket: WebSocketServerProtocol) -> None:
        if self.debug_mode:
            await self.new_log(f"[yellow bold]DEBUG:[/yellow bold] Received flag message: {data}")
        conn = sqlite3.connect(self.db_filename)
        cursor = conn.cursor()

        content = data.get('content', {})
        action = content.get('action')
        sender = data.get('from')
        teamname = self.user_to_team.get(sender)

        if not teamname:
            await websocket.send(json.dumps({
                'type': 'flag',
                'status': 'rejected',
                'content': "You must be in a team to submit flags",
                'timestamp': time.time()
            }))
            await self.new_log(f"[red bold]ERROR:[/red bold] User `{sender}` not in a team")
            conn.close()
            return

        if action == 'submit':
            challenge_name = content.get('challenge_name')
            flag_value = content.get('flag_value')
            flag_points = content.get('flag_points')

            if not all([challenge_name, flag_value, flag_points is not None]):
                await self.new_log(f"[red bold]ERROR:[/red bold] Missing required flag fields: {content}")
                await websocket.send(json.dumps({
                    'type': 'flag',
                    'status': 'rejected',
                    'content': "Missing required flag fields",
                    'timestamp': time.time()
                }))
                conn.close()
                return

            try:
                flag_points = int(flag_points)
            except (ValueError, TypeError):
                await self.new_log(f"[red bold]ERROR:[/red bold] Invalid flag_points value: {flag_points}")
                await websocket.send(json.dumps({
                    'type': 'flag',
                    'status': 'rejected',
                    'content': "Invalid points value",
                    'timestamp': time.time()
                }))
                conn.close()
                return

            try:
                cursor.execute(
                    "INSERT INTO flag (challenge_name, flag_value, points, teamname) VALUES (?, ?, ?, ?)",
                    (challenge_name, flag_value, flag_points, teamname)
                )
                conn.commit()
                await self.new_log(
                    f"[green bold]INFO:[/green bold] Flag submitted by `{sender}`: {challenge_name} - {flag_value} - {flag_points} points"
                )
                message = f"🚩 [green bold]Flag submitted:[/green bold] {challenge_name} - {flag_value} - {flag_points} points"
                await websocket.send(json.dumps({
                    'type': 'flag',
                    'status': 'accepted',
                    'content': message,
                    'timestamp': time.time()
                }))
                if teamname and data.get('breakroom'):
                    await self.send_team_message(teamname, message)
                else:
                    await self.send_announcement(message)
            except sqlite3.Error as e:
                await self.new_log(f"[red bold]ERROR:[/red bold] Database error during flag submission: {e}")
                await websocket.send(json.dumps({
                    'type': 'flag',
                    'status': 'rejected',
                    'content': f"Database error: {e}",
                    'timestamp': time.time()
                }))
            finally:
                conn.close()

        elif action == 'show':
            try:
                if teamname and data.get('breakroom'):
                    cursor.execute("SELECT * FROM flag WHERE teamname = ?", (teamname,))
                else:
                    cursor.execute("SELECT * FROM flag")
                flag_list = cursor.fetchall()
                payload = {
                    'type': 'flags',
                    'from': 'server',
                    'content': flag_list,
                    'timestamp': time.time()
                }
                await websocket.send(json.dumps(payload))
            except sqlite3.Error as e:
                await self.new_log(f"[red bold]ERROR:[/red bold] Database error during flag retrieval: {e}")
                await websocket.send(json.dumps({
                    'type': 'flag',
                    'status': 'rejected',
                    'content': f"Database error: {e}",
                    'timestamp': time.time()
                }))
            finally:
                conn.close()

    async def handle_pm(self, data: MessageData, websocket: WebSocketServerProtocol) -> None:
        target_user = data.get('to')
        sender = data.get('from')
        message = data.get('content')
        teamname = self.user_to_team.get(sender)

        if not target_user or not message:
            await self.new_log(f"[red bold]ERROR:[/red bold] Invalid private message format from {sender}")
            return

        if target_user not in self.connections:
            await self.new_log(f"[red bold]ERROR:[/red bold] Target user `{target_user}` not found")
            error_payload = {
                'type': 'pm',
                'from': 'server',
                'to': sender,
                'content': f"User `{target_user}` is not connected",
                'timestamp': time.time()
            }
            await self.connections[sender].send(json.dumps(error_payload))
            return

        if data.get('breakroom') and teamname:
            if self.user_to_team.get(target_user) != teamname:
                await self.new_log(f"[red bold]ERROR:[/red bold] Target user `{target_user}` not in team `{teamname}`")
                error_payload = {
                    'type': 'pm',
                    'from': 'server',
                    'to': sender,
                    'content': f"User `{target_user}` is not in your team",
                    'timestamp': time.time()
                }
                await self.connections[sender].send(json.dumps(error_payload))
                return

        if self.debug_mode:
            await self.new_log(f"[yellow bold]DEBUG:[/yellow bold] Private message from `{sender}` to `{target_user}`: {message}")

        payload = {
            'type': 'pm',
            'from': sender,
            'to': target_user,
            'content': message,
            'timestamp': time.time(),
            'breakroom': data.get('breakroom', False)
        }
        try:
            await self.connections[target_user].send(json.dumps(payload))
            await self.new_log(f"[green bold]INFO:[/green bold] Private message sent from `{sender}` to `{target_user}`")
        except websockets.exceptions.ConnectionClosed:
            await self.new_log(f"[yellow bold]NOTICE:[/yellow bold] Couldn't reach {target_user}")
            error_payload = {
                'type': 'pm',
                'from': 'server',
                'to': sender,
                'content': f"User `{target_user}` is disconnected",
                'timestamp': time.time()
            }
            await self.connections[sender].send(json.dumps(error_payload))

    async def handle_team(self, data: MessageData, websocket: WebSocketServerProtocol) -> None:
        action = data.get('action')
        sender = data.get('from')

        if action == 'create':
            teamname = data.get('teamname')
            password = data.get('password')
            if not teamname or not password:
                await self.new_log(f"[red bold]ERROR:[/red bold] Missing teamname or password from `{sender}`")
                await websocket.send(json.dumps({
                    'type': 'team',
                    'action': 'create',
                    'status': 'rejected',
                    'content': f"Missing teamname or password",
                    'timestamp': time.time()
                }))
                return
            if teamname in self.teams or teamname in self.pending_teams:
                await self.new_log(f"[red bold]ERROR:[/red bold] Team `{teamname}` already exists")
                await websocket.send(json.dumps({
                    'type': 'team',
                    'action': 'create',
                    'status': 'rejected',
                    'content': f"Team `{teamname}` already exists",
                    'timestamp': time.time()
                }))
                return
            self.pending_teams[teamname] = {
                'password': password,
                'leader': sender
            }
            await self.new_log(f"[green bold]INFO:[/green bold] Team creation request for `{teamname}` by `{sender}`")
            await websocket.send(json.dumps({
                'type': 'team',
                'action': 'create',
                'status': 'pending',
                'teamname': teamname,
                'timestamp': time.time()
            }))

        elif action == 'join':
            teamname = data.get('teamname')
            password = data.get('password')
            if not teamname or not password:
                await self.new_log(f"[red bold]ERROR:[/red bold] Missing teamname or password from `{sender}`")
                await websocket.send(json.dumps({
                    'type': 'team',
                    'action': 'join',
                    'status': 'rejected',
                    'content': f"Missing teamname or password",
                    'timestamp': time.time()
                }))
                return
            if teamname not in self.teams:
                await self.new_log(f"[red bold]ERROR:[/red bold] Team `{teamname}` not found")
                await websocket.send(json.dumps({
                    'type': 'team',
                    'action': 'join',
                    'status': 'rejected',
                    'content': f"Team `{teamname}` not found",
                    'timestamp': time.time()
                }))
                return
            if self.teams[teamname]['password'] != password:
                await self.new_log(f"[red bold]ERROR:[/red bold] Incorrect password for team `{teamname}`")
                await websocket.send(json.dumps({
                    'type': 'team',
                    'action': 'join',
                    'status': 'rejected',
                    'content': "Incorrect password",
                    'timestamp': time.time()
                }))
                return
            self.teams[teamname]['members'].append(sender)
            self.user_to_team[sender] = teamname
            await self.new_log(f"[green bold]INFO:[/green bold] User `{sender}` joined team `{teamname}`")
            await websocket.send(json.dumps({
                'type': 'team',
                'action': 'join',
                'status': 'accepted',
                'teamname': teamname,
                'timestamp': time.time()
            }))
            await self.send_team_message(teamname, f"User `{sender}` has joined the team")

        elif action == 'kick':
            teamname = data.get('teamname')
            target_user = data.get('target_user')
            if self.teams.get(teamname, {}).get('leader') != sender:
                await self.new_log(f"[red bold]ERROR:[/red bold] Only team leader can kick members")
                await websocket.send(json.dumps({
                    'type': 'team',
                    'action': 'kick',
                    'status': 'rejected',
                    'content': "Only team leader can kick members",
                    'timestamp': time.time()
                }))
                return
            if target_user not in self.teams[teamname]['members']:
                await self.new_log(f"[red bold]ERROR:[/red bold] User `{target_user}` not in team `{teamname}`")
                await websocket.send(json.dumps({
                    'type': 'team',
                    'action': 'kick',
                    'status': 'rejected',
                    'content': f"User `{target_user}` not in team",
                    'timestamp': time.time()
                }))
                return
            self.teams[teamname]['members'].remove(target_user)
            if not self.teams[teamname]['members']:
                del self.teams[teamname]
            del self.user_to_team[target_user]
            await self.new_log(f"[green bold]INFO:[/green bold] User `{target_user}` kicked from team `{teamname}`")
            await self.send_team_message(teamname, f"User `{target_user}` has been kicked")
            if target_user in self.connections:
                await self.connections[target_user].send(json.dumps({
                    'type': 'team',
                    'action': 'kick',
                    'status': 'accepted',
                    'content': f"You have been kicked from team `{teamname}`",
                    'timestamp': time.time()
                }))

        elif action == 'list':
            payload = {
                'type': 'team',
                'action': 'list',
                'content': [
                    {'teamname': teamname, 'leader': info['leader'], 'members': info['members']}
                    for teamname, info in self.teams.items()
                ],
                'timestamp': time.time()
            }
            await websocket.send(json.dumps(payload))

        elif action == 'listteams':
            payload = {
                'type': 'team',
                'action': 'listteams',
                'content': list(self.teams.keys()),
                'timestamp': time.time()
            }
            await websocket.send(json.dumps(payload))
