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
from datetime import datetime
from websockets.server import WebSocketServerProtocol

DEFAULT_PORT = 1107

console = Console(force_terminal=True, color_system="truecolor", file=sys.stdout)
session = PromptSession(style=Style.from_dict({
    'prompt': 'fg:red bold', 
}))

MessageData = Dict[str, Any]

'''
Message data structure
{
    "type": "msg|file|cmd|auth|announce"
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
        self.session_start = datetime.now()
        
        # Create database file with session timestamp
        self.db_filename = f"session_{self.session_start.strftime('%Y%m%d_%H%M%S')}.db"
        self._create_database()
        
        # Stores client connections in a dictionary
        self.connections: Dict[str, WebSocketServerProtocol] = {}
        self.wait_list: Dict[str, WebSocketServerProtocol] = {}

        self.server: Optional[websockets.WebSocketServer] = None

        # store message methods
        self.handlers: Dict[str, Callable[[MessageData, WebSocketServerProtocol], Awaitable[None]]] = {
            'msg': self.send_msg,
            'auth': self.authenticate,
            'flag': self.handle_flag,
        }

        # store command methods
        self.commands: Dict[str, Callable[[List[str]], Awaitable[None]]] = {
            'auth': self.cmd_auth,
            'users': self.cmd_users,
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
        Starts the websocket server and prints starting logs

        Args:
            host (string): interface to listen to
            port (int): port to listen to
        """
        self.server = await websockets.serve(self.handler, self.host, self.port)
        print(open(".radioactive", "r").read().replace('\\x1b', '\x1b').replace('\\n', '\n'))
        await self.new_log(f"Radioactive Tinapay running at: [magenta bold]{self.host}:{self.port}[/magenta bold]\n")
        
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
            except (websockets.exceptions.ConnectionClosedOK, websockets.exceptions.ConnectionClosedError) as e:
                # Remove client from connection storage if received disconnect
                for client in self.connections.keys():
                    if self.connections[client] == websocket:
                        await self.new_log(f"[green bold]INFO:[/green bold] Received disconnect from user: `{client}`")
                        await self.send_announcement(f"User `{client}` has disconnected")
                        del self.connections[client]
                        break

                # Remove client from waitlist if received disconnect
                for client in self.wait_list.keys():
                    if self.wait_list[client] == websocket:
                        await self.new_log(f"[green bold]INFO:[/green bold] Received disconnect from waitlist user `{client}`")
                        await self.send_announcement(f"Waitlist user `{client}` has disconnected")
                        del self.wait_list[client]
                        break
                return

            data = json.loads(data)

            # Check if client is authenticated
            if data.get('from') not in self.connections.keys() and data.get('type') != 'auth':
                return

            if data.get('type') == 'msg' and data.get('to') == None:
                await self.new_log(f"Message from `{data.get('from')}`: {data.get('content')}")
                await self.send_msg(data)
            elif data.get('type') in self.handlers:
                await self.handlers[data.get('type')](data, websocket)

    async def new_log(self, log: str) -> None:
        """
        Adds a new message entry into chat history.
        Could be a user message or server broadcast

        Args:
            msg (string): Message to be added
        """
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

    async def command_loop(self) -> None:
        """
        Command loop for the server
        """
        with patch_stdout():
            while True:
                prompt = await session.prompt_async("(server)> ")
                tokens = prompt.strip().split()
                if not tokens:
                    continue

                command = tokens[0][1:]  # Remove the leading '/'

                if command == 'stop':
                    await self.cmd_stop()
                    return

                if command in self.commands:
                    await self.commands[command](tokens)
                else:
                    await self.new_log(f"[red bold]ERROR:[/red bold] Unknown command: {command}")

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
            # Don't send message to sender
            if client_name != sender:
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
        conn = sqlite3.connect(self.db_filename)
        cursor = conn.cursor()

        if data.get('content').get('action') == 'submit':
            cursor.execute("INSERT INTO flag (challenge_name, flag_value, points) VALUES (?, ?, ?)", (data.get('content').get('challenge_name'), data.get('content').get('flag_value'), data.get('content').get('flag_points')))
            conn.commit()
            await self.send_announcement(f"🚩 [green bold]Flag submitted:[/green bold] {data.get('content').get('challenge_name')} - {data.get('content').get('flag_value')} - {data.get('content').get('flag_points')} points")
        elif data.get('content').get('action') == 'show':
            flags = cursor.execute("SELECT * FROM flag")
            flag_list = flags.fetchall()
            
            payload = {
                'type': 'flags',
                'from': 'server',
                'content': flag_list,
                'timestamp': time.time()
            }

            await self.send_msg(payload, to=data.get('from'))

        conn.close()
