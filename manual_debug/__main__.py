import argparse
from pathlib import Path

from dotenv import load_dotenv
import uvicorn

# Load .env from manual_debug dir so ELEVENLABS_API_KEY is available for lip-sync test
load_dotenv(Path(__file__).resolve().parent / ".env")

from .app import create_app


def main():
    parser = argparse.ArgumentParser(description="Manual Debug — Humanoid Robot")
    parser.add_argument("--port", default=None, help="Serial port (auto-detect if omitted)")
    parser.add_argument("--config", default=None, help="Path to servo_data.json")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP host (default 0.0.0.0)")
    parser.add_argument("--http-port", type=int, default=8000, help="HTTP port (default 8000)")
    args = parser.parse_args()

    app = create_app(config_path=args.config, serial_port=args.port)
    print(f"Starting Manual Debug UI at http://localhost:{args.http_port}")
    uvicorn.run(app, host=args.host, port=args.http_port)


if __name__ == "__main__":
    main()
