"""Permet `python -m ddd ...` sans installation."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
