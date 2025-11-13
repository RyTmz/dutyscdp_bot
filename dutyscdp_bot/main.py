from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from .bot import DutyBot
from .config import load_config
from .loop_client import LoopClient
from .server import WebhookServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def _run(config_path: str, webhook_host: str, webhook_port: int) -> None:
    config = load_config(config_path)
    client = LoopClient(token=config.loop.token, base_url=config.loop.server_url, team=config.loop.team)
    bot = DutyBot(config=config, client=client)
    loop = asyncio.get_running_loop()
    server = WebhookServer(bot, loop, host=webhook_host, port=webhook_port)
    server.start()

    stop_event = asyncio.Event()

    def _handle_signal(*_: int) -> None:
        bot.stop()
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            logging.getLogger(__name__).warning("Signal handlers not supported on this platform; use Ctrl+C to stop")
            break

    await asyncio.gather(bot.start(), stop_event.wait())
    server.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Duty scheduler bot")
    parser.add_argument("--config", default="config.toml", help="Path to config file")
    parser.add_argument("--webhook-host", default="0.0.0.0")
    parser.add_argument("--webhook-port", type=int, default=8080)
    args = parser.parse_args()
    asyncio.run(_run(args.config, args.webhook_host, args.webhook_port))


if __name__ == "__main__":
    main()
