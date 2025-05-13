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

console = Console(force_terminal=True, color_system="truecolor", file=sys.stdout)
session = PromptSession(style=Style.from_dict({
    'prompt': 'fg:red bold',
}))

MessageData = Dict[str, Any]

'''
Message data structure
{
    "type": "msg|file|cmd|auth|announce|flags"
    "from": "<username>"
    "to": "<username>"
    "timestamp": "<time>"
    "content": "<msg|file|cmd>"
}
'''

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

        # Stores client connections in a dictionary
        self.connections: Dict[str, WebSocketServerProtocol] = {}
        self.wait_list: Dict[str, WebSocketServerProtocol] = {}

        self.server: Optional[websockets.WebSocketServer] = None

        # Store message methods
        self.handlers: Dict[str, Callable[[MessageData, WebSocketServerProtocol], Awaitable[None]]] = {
            'msg': self.send_msg,
            'auth': self.authenticate,
            'flag': self.handle_flag,
        }

        # Store command methods
        self.commands: Dict[str, Callable[[List[str]], Awaitable[None]]] = {
            'auth': self.cmd_auth,
            'users': self.cmd_users,
            'flag': self.cmd_flag,
            'help': self.cmd_help,
            'clear': self.cmd_clear,
            'multiline': self.cmd_multiline,
        }

        self.host: str = server_addr
        self.port: int = DEFAULT_PORT

        if ":" in server_addr:
            components = server_addr.split(":")
            self.host = components[0]
            self.port = int(components[1])

        console.log("☢️ Starting Radioactive Tinapay server\n")

    def _get_session_name(self) -> str:
        """Get session name from user input"""
        while True:
            name = input("Enter session name: ").strip()
            if name:
                return name
            console.print("[red]Session name cannot be empty![/red]")

    def _get_session_details(self) -> str:
        """Get session details from user input"""
        while True:
            details = input("Enter session details: ").strip()
            if details:
                return details
            console.print("[red]Session details cannot be empty![/red]")

    def _get_max_users(self) -> int:
        """Get maximum number of users from user input"""
        while True:
            try:
                max_users = int(input("Enter maximum number of users: ").strip())
                if max_users > 0:
                    return max_users
                console.print("[red]Maximum users must be greater than 0![/red]")
            except ValueError:
                console.print("[red]Please enter a valid number![/red]")

    def _get_debug_mode(self) -> bool:
        """Prompt for debug mode (y/N)"""
        while True:
            response = input("Enable debug mode? (y/N): ").strip().lower()
            if response in ['y', 'n', '']:
                return response == 'y'
            console.print("[red]Please enter 'y' or 'N' (or press Enter for 'N')![/red]")

    def _create_database(self) -> None:
        """Create the session database file with required tables"""
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

        # Create flag table with challenge_name, flag_value, and points
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS flag (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            challenge_name TEXT NOT NULL,
            flag_value TEXT NOT NULL,
            points INTEGER NOT NULL,
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
        """
        Starts the websocket server with SSL/TLS and prints starting logs

        Args:
            host (string): interface to listen to
            port (int): port to listen to
        """
        # Create SSL context
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert_file = "server.crt"  # Path to certificate
        key_file = "server.key"   # Path to private key

        # Check if certificate and key files exist
        if not os.path.exists(cert_file) or not os.path.exists(key_file):
            console.print("[red bold]ERROR:[/red bold] SSL certificate or key file not found. Please generate server.crt and server.key.")
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
        """
        Stops the websocket server and closes all client connections
        """
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
        """
        Websocket connection handler

        Args:
            websocket (websocket): The websocket object of the client
        """
        while True:
            try:
                data = await websocket.recv()
                if self.debug_mode:
                    await self.new_log(f"[yellow bold]DEBUG:[/yellow bold] Received WebSocket message: {data}")
            except (websockets.exceptions.ConnectionClosedOK, websockets.exceptions.ConnectionClosedError) as e:
                for client in list(self.connections.keys()):
                    if self.connections.get(client) == websocket:
                        await self.new_log(f"[green bold]INFO:[/green bold] Received disconnect from user: `{client}`")
                        await self.send_announcement(f"User `{client}` has disconnected")
                        if client in self.connections:
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

            if data.get('from') not in self.connections.keys() and data.get('type') != 'auth':
                await self.new_log(f"[red bold]ERROR:[/red bold] Unauthenticated client: {data.get('from')}")
                return

            if data.get('type') == 'msg' and data.get('to') == None:
                await self.new_log(f"Message from `{data.get('from')}`: {data.get('content')}")
                await self.send_msg(data)
            elif data.get('type') in self.handlers:
                await self.handlers[data.get('type')](data, websocket)
            else:
                await self.new_log(f"[red bold]ERROR:[/red bold] Unknown message type: {data.get('type')}")

    async def new_log(self, log: str) -> None:
        """
        Adds a new message entry into chat history.
        Could be a user message or server broadcast

        Args:
            msg (string): Message to be added
        """
        if '[yellow bold]DEBUG:[/yellow bold]' in log and not self.debug_mode:
            return
        await run_in_terminal(lambda: console.log(log))

    async def cmd_auth(self, tokens: List[str]) -> None:
        """Handle authentication commands"""
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

                # Send accepted status to client
                response: MessageData = {
                    'type': 'auth',
                    'status': 'accepted',
                    'from': client
                }
                await self.wait_list[client].send(json.dumps(response))

                # Broadcast join announcement
                await self.send_announcement(f"User `{client}` has joined")

                del self.wait_list[client]
            else:
                await self.new_log(f"[red bold]ERROR:[/red bold] Client `{client}` not in wait list")
        elif action == 'reject':
            if client in self.wait_list:
                # Send rejected status to client
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
        """Handle user management commands"""
        if len(tokens) < 2:
            await self.new_log("[red bold]ERROR:[/red bold] Invalid users command format")
            await self.new_log("Usage: /users \\[list|kick] <username>")
            return

        action = tokens[1]

        if action == 'list':
            # Create a table for connected users
            table = Table(title="Connected Users")
            table.add_column("Username", style="cyan")
            table.add_column("Status", style="green")

            if not self.connections:
                table.add_row("No users", "No connections")
            else:
                for username in self.connections:
                    table.add_row(username, "Connected")

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
                await self.connections[client].close()
                del self.connections[client]
            else:
                await self.new_log(f"[red bold]ERROR:[/red bold] User `{client}` not found")

    async def cmd_flag(self, tokens: List[str]) -> None:
        """Handle flag submission and display"""
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

            # Store flag in database
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
            # Retrieve and display flags
            conn = sqlite3.connect(self.db_filename)
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT * FROM flag")
                flag_list = cursor.fetchall()
                table = Table(title="[yellow bold]🚩 Flags:[/yellow bold]")
                table.add_column("Challenge Name", style="cyan")
                table.add_column("Flag Value", style="green")
                table.add_column("Points", style="red")

                if not flag_list:
                    table.add_row("No flags", "Empty", "0")
                else:
                    for flag in flag_list:
                        table.add_row(flag[1], flag[2], str(flag[3]))

                console.print(table)
            except sqlite3.Error as e:
                await self.new_log(f"[red bold]ERROR:[/red bold] Database error during flag retrieval: {e}")
            finally:
                conn.close()

        else:
            await self.new_log("[red bold]ERROR:[/red bold] Invalid flag action")
            await self.new_log("Usage: /flag \\[submit|show] <challenge_name> <flag_value> <points>")

    async def cmd_help(self, tokens: List[str]) -> None:
        """Display all available server commands"""
        table = Table(title="[yellow bold]Server Commands[/yellow bold]")
        table.add_column("Command", style="cyan")
        table.add_column("Usage", style="green")

        commands = [
            ("/auth", "/auth [accept|reject|list] <username> - Manage client authentication"),
            ("/users", "/users [list|kick] <username> - Manage connected users"),
            ("/flag", "/flag [submit|show] <challenge_name> <flag_value> <points> - Submit or display flags"),
            ("/multiline", "/multiline - Enter multiline mode (type 'END' to send)"),
            ("/help", "/help - Display this help message"),
            ("/clear", "/clear - Clear the terminal screen"),
            ("/stop", "/stop - Shut down the server")
        ]

        for cmd, usage in commands:
            table.add_row(cmd, usage)

        console.print(table)

    async def cmd_clear(self, tokens: List[str]) -> None:
        """Clear the terminal screen"""
        console.clear()
        await self.new_log("[green bold]INFO:[/green bold] Terminal cleared")

    async def cmd_multiline(self, tokens: List[str]) -> None:
        """Toggle multiline mode"""
        if self.multiline_mode:
            await self.new_log("[yellow bold]Already in multiline mode. Type 'END' on a new line to send.[/yellow bold]")
        else:
            self.multiline_mode = True
            self.multiline_buffer = []
            await self.new_log("[yellow bold]Multiline mode on. Type 'END' on a new line to send the message.[/yellow bold]")

    async def send_server_message(self, message: str) -> None:
        """
        Sends a chat message from the server to all clients

        Args:
            message (str): The message to send
        """
        payload: MessageData = {
            'type': 'msg',
            'from': 'server',
            'content': message,
            'timestamp': time.time()
        }
        await self.new_log(f"Message from `server`: {message}")
        await self.send_msg(payload)

    async def command_loop(self) -> None:
        """
        Command loop for the server
        """
        with patch_stdout():
            while True:
                prompt = MULTILINE_PROMPT if self.multiline_mode else PROMPT
                input_str = await session.prompt_async(prompt)
                if self.multiline_mode:
                    if input_str.strip().upper() == "END":
                        # Exit multiline mode and send the message
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
                        command = tokens[0][1:]  # Remove the leading '/'
                        if command == 'stop':
                            await self.cmd_stop()
                            return
                        if command in self.commands:
                            await self.commands[command](tokens)
                        else:
                            await self.new_log(f"[red bold]ERROR:[/red bold] Unknown command: {command}")
                    else:
                        # Treat non-command input as a chat message
                        message = input_str.strip()
                        if message:
                            await self.send_server_message(message)

    async def authenticate(self, data: MessageData, websocket: WebSocketServerProtocol) -> None:
        """
        Authenticate to a server

        Args:
            data (dict): Authentication metadata
            websocket (websocket): The websocket object of the client
        """
        try:
            if data.get('from') not in self.connections:
                self.wait_list[data.get('from')] = websocket
                await self.new_log(f"[green bold]INFO:[/green bold] ⌛ Auth request from `{data.get('from')}`")

                # Send pending status to client
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
        """
        Sends message to (a) client(s)

        Args:
            data (dict): Message metadata
            to (string): User(s) to send message to
        """
        data['timestamp'] = time.time()
        sender = data['from']
        data = json.dumps(data)

        # Broadcast message to all clients
        for client_name, client in self.connections.items():
            # Don't send message to sender (unless it's the server)
            if client_name != sender or sender == 'server':
                try:
                    await client.send(data)
                except websockets.exceptions.ConnectionClosed:
                    console.log(f"[yellow bold][=] NOTICE:[/yellow bold] Couldn't reach {client_name}")

    async def send_announcement(self, message: str) -> None:
        """
        Sends an announcement to all connected clients

        Args:
            message (str): The announcement message to broadcast
        """
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
        """
        Handle flag submission from clients

        Args:
            data (dict): Flag submission data
            websocket (websocket): The websocket object of the client
        """
        if self.debug_mode:
            await self.new_log(f"[yellow bold]DEBUG:[/yellow bold] Received flag message: {data}")
        conn = sqlite3.connect(self.db_filename)
        cursor = conn.cursor()

        content = data.get('content', {})
        action = content.get('action')

        if action == 'submit':
            challenge_name = content.get('challenge_name')
            flag_value = content.get('flag_value')
            flag_points = content.get('flag_points')

            # Validate input
            if not all([challenge_name, flag_value, flag_points is not None]):
                await self.new_log(f"[red bold]ERROR:[/red bold] Missing required flag fields: {content}")
                conn.close()
                return

            try:
                flag_points = int(flag_points)  # Ensure flag_points is an integer
            except (ValueError, TypeError):
                await self.new_log(f"[red bold]ERROR:[/red bold] Invalid flag_points value: {flag_points}")
                conn.close()
                return

            try:
                cursor.execute(
                    "INSERT INTO flag (challenge_name, flag_value, points) VALUES (?, ?, ?)",
                    (challenge_name, flag_value, flag_points)
                )
                conn.commit()
                await self.new_log(
                    f"[green bold]INFO:[/green bold] Flag submitted: {challenge_name} - {flag_value} - {flag_points} points"
                )
                await self.send_announcement(
                    f"🚩 [green bold]Flag submitted:[/green bold] {challenge_name} - {flag_value} - {flag_points} points"
                )
            except sqlite3.Error as e:
                await self.new_log(f"[red bold]ERROR:[/red bold] Database error during flag submission: {e}")
            finally:
                conn.close()

        elif action == 'show':
            try:
                cursor.execute("SELECT * FROM flag")
                flag_list = cursor.fetchall()
                payload = {
                    'type': 'flags',
                    'from': 'server',
                    'content': flag_list,
                    'timestamp': time.time()
                }
                await self.send_msg(payload, to=data.get('from'))
            except sqlite3.Error as e:
                await self.new_log(f"[red bold]ERROR:[/red bold] Database error during flag retrieval: {e}")
            finally:
                conn.close()

        else:
            await self.new_log(f"[red bold]ERROR:[/red bold] Invalid flag action: {action}")
            conn.close()
