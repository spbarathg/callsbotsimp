#!/usr/bin/env python3
"""
Standalone executor runner
Run this as a separate process from your monitor
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from exec.executor import MemecoinExecutor
from exec.config import load_executor_settings


def setup_logging():
    """Setup logging for executor"""
    
    log_format = '%(asctime)s | %(levelname)-8s | %(name)-12s | %(message)s'
    
    # Create logs directory
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            # Console output
            logging.StreamHandler(sys.stdout),
            # File output
            logging.FileHandler(log_dir / "executor.log"),
            # Error file
            logging.FileHandler(log_dir / "executor_errors.log", 
                              encoding='utf-8')
        ]
    )
    
    # Set specific log levels
    logging.getLogger('aiohttp').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)


def validate_environment():
    """Validate required environment variables"""
    
    required_vars = [
        'EXECUTOR_PRIVATE_KEY',
        'EXECUTOR_RPC_URL'
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print("📋 Copy exec/env_example.txt to .env and fill in your values")
        return False
    
    return True


async def run_executor():
    """Main executor runner"""
    
    try:
        # Load settings
        settings = load_executor_settings()
        
        # Create and start executor
        executor = MemecoinExecutor(settings)
        
        logging.info("🚀 Starting Memecoin Executor...")
        logging.info(f"💰 Base position size: ${settings.base_position_size_usd}")
        logging.info(f"🛡️  Daily loss limit: {settings.daily_loss_limit_pct:.1%}")
        logging.info(f"📊 Max positions: {settings.max_concurrent_positions}")
        
        await executor.start()
        
    except KeyboardInterrupt:
        logging.info("👋 Executor stopped by user")
        return 0
    except Exception as e:
        logging.error(f"💥 Executor failed: {e}", exc_info=True)
        return 1


def main():
    """Entry point"""
    
    # Setup logging first
    setup_logging()
    
    # Load environment file if it exists
    env_file = Path('.env')
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv()
        logging.info("📁 Loaded .env file")
    else:
        logging.warning("⚠️  No .env file found, using system environment")
    
    # Validate environment (after loading .env)
    if not validate_environment():
        return 1
    
    # Run executor
    try:
        exit_code = asyncio.run(run_executor())
        return exit_code
    except Exception as e:
        logging.error(f"💥 Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
