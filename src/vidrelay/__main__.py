"""允许 `python -m vidrelay` 调用。"""

from vidrelay.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
