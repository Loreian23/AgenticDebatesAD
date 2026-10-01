"""Entry point for Agent Debate System."""

import logging
import os
import sys

# Configure logging before anything else
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.api import app
from src.database import init_db

if __name__ == "__main__":
    import uvicorn
    
    # Initialize database
    init_db()
    
    # Run server
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    reload = os.getenv("RELOAD", "false").lower() == "true"
    
    logger.info("Starting Agent Debate System on http://%s:%s", host, port)
    logger.info("API docs available at http://%s:%s/docs", host, port)
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=reload,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )
