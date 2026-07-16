"""FDEM application logging."""

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def _setup() -> logging.Logger:
    logger = logging.getLogger("FDEM")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler = RotatingFileHandler(
        LOG_DIR / "fdem.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)
    logger.addHandler(console)
    return logger


log = _setup()


def log_exception(exc: Exception, context: str = "") -> None:
    message = f"{context}: {exc}" if context else str(exc)
    log.error("%s\n%s", message, traceback.format_exc())


def log_ssh(command: str, ok: bool, stdout: str = "", stderr: str = "") -> None:
    log.log(
        logging.INFO if ok else logging.ERROR,
        "SSH %s -> %s stdout=%s stderr=%s",
        command[:120], "OK" if ok else "FAIL", stdout[:200], stderr[:200],
    )
