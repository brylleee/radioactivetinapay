# ☢️ Radioactive Tinapay

A chat system developed by A1SBERG. This project implements a client-server architecture for CTF communications.

## Features

- Client-server architecture for communications
- WebSocket-based communication
- Rich terminal interface with prompt toolkit
- Cross-platform compatibility

## Prerequisites

- Python 3.7 or higher
- Required Python packages (see `requirements.txt`)

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/radioactivetinapay.git
cd radioactivetinapay
```

2. Install the required dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Server Mode

To run the application in server mode:

```bash
python main.py server -l IP[:PORT]
```

- `IP`: The IP address to listen on
- `PORT`: (Optional) The port to listen on (default: 1107)

### Client Mode

To run the application in client mode:

```bash
python main.py client -h IP[:PORT]
```

- `IP`: The server's IP address to connect to
- `PORT`: (Optional) The server's port (default: 1107)

### Additional Options

- `-c file`: Specify a custom configuration file (default: `radtinconf.yml`)

## Project Structure

- `main.py`: Entry point and command-line interface
- `server.py`: Server implementation
- `client.py`: Client implementation
- `requirements.txt`: Python package dependencies

## Dependencies

- `rich`: Terminal formatting and styling
- `prompt_toolkit`: Interactive command-line interface
- `websockets`: WebSocket client and server implementation

## License

This project is licensed under the terms included in the `LICENSE` file.
