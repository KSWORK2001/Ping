"""File logger that flushes every record (so we can read it live while the
detached bot runs). Writes to ping_debug.log in the working directory."""
import logging
import os

import config

logger = logging.getLogger("ping")
_configured = False


def setup():
    global _configured
    if _configured:
        return logger
    logger.setLevel(logging.INFO)
    path = os.path.join(config.WORKDIR, "ping_debug.log")
    fh = logging.FileHandler(path, encoding="utf-8")  # flushes per record
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)
    _configured = True
    logger.info("=== logging started -> %s ===", path)
    return logger
