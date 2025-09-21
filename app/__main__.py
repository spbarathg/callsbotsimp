import asyncio
import logging
import os
from dotenv import load_dotenv

from .config import load_settings
from .monitor import Monitor


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    load_dotenv()
    # Windows-specific selector policy if available; avoid mutating policy in libraries
    try:
        import os as _os
        if _os.name == "nt":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass
    setup_logging()
    settings = load_settings()

    # Ensure data directory exists for session and caches
    os.makedirs(settings.data_dir, exist_ok=True)

    if settings.api_id == 0 or not settings.api_hash:
        logging.error("API_ID/API_HASH are required. Set them in environment or .env")
        return

    if not settings.monitored_groups:
        logging.warning("MONITORED_GROUPS is empty. Set a comma-separated list in .env")

    monitor = Monitor(settings)
    asyncio.run(monitor.start())


if __name__ == "__main__":
    main()
