#!/usr/bin/env python3
"""Centralised logging for the sonifier.

One place to configure how the daemon and CLI log, so every module just does::

    from logging_setup import get_logger
    log = get_logger(__name__)

and debugging is a matter of raising the level or tailing the log file. Thin
wrapper over stdlib ``logging`` — no framework.

Design (serves "log everything so it's easier to debug"):
  * the console (stderr) shows INFO+ by default, so the daemon's normal output
    stays readable;
  * the log FILE, when one is configured, always captures DEBUG regardless of
    console level — so a post-mortem has the full firehose without the live
    console being spammed.

Env:
  SONIFIER_LOG_LEVEL   console level: DEBUG/INFO/WARNING/ERROR (default INFO,
                       or WARNING when the daemon runs with SONIFIER_QUIET).
  SONIFIER_LOG_FILE    path to a rotating debug log (2MB x 3). If unset, no
                       file handler is added (console only).
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_ROOT = "sonifier"
_configured = False


def get_logger(name: str = _ROOT) -> logging.Logger:
    """Return a logger under the ``sonifier`` tree. Pass ``__name__`` and it is
    normalised to ``sonifier.<module>`` so records share one parent/handlers."""
    if name == _ROOT or name.startswith(_ROOT + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT}.{name.rsplit('.', 1)[-1]}")


def _console_level(quiet: bool) -> int:
    name = os.environ.get("SONIFIER_LOG_LEVEL", "").strip().upper()
    if name:
        return getattr(logging, name, logging.INFO)
    return logging.WARNING if quiet else logging.INFO


def configure(quiet: bool = False, log_file: str | None = None) -> logging.Logger:
    """Install handlers on the ``sonifier`` logger (idempotent). Call once from
    the process entry point (main). Returns the root sonifier logger."""
    global _configured
    root = logging.getLogger(_ROOT)
    if _configured:
        return root

    root.setLevel(logging.DEBUG)  # capture all; handlers decide what emits
    root.propagate = False        # don't double-log through the stdlib root
    fmt = logging.Formatter(_FORMAT)

    console = logging.StreamHandler()  # stderr
    console.setLevel(_console_level(quiet))
    console.setFormatter(fmt)
    root.addHandler(console)

    path = log_file or os.environ.get("SONIFIER_LOG_FILE")
    if path:
        fh = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3)
        fh.setLevel(logging.DEBUG)  # file gets everything, always
        fh.setFormatter(fmt)
        root.addHandler(fh)

    _configured = True
    return root


if __name__ == "__main__":
    # ponytail: one runnable self-check — file gets DEBUG even when console is quiet.
    import tempfile

    with tempfile.NamedTemporaryFile("r", suffix=".log", delete=False) as tf:
        path = tf.name
    os.environ["SONIFIER_LOG_LEVEL"] = "WARNING"
    configure(log_file=path)
    log = get_logger("pkg.logging_setup")
    assert log.name == "sonifier.logging_setup"
    log.debug("debug-line")
    log.warning("warn-line")
    for h in logging.getLogger(_ROOT).handlers:
        h.flush()
    body = open(path).read()
    assert "debug-line" in body, "file handler must capture DEBUG"
    assert "warn-line" in body
    assert configure() is logging.getLogger(_ROOT)  # idempotent
    os.remove(path)
    print("logging_setup self-check ok")
