"""支持 `python -m techradar` 运行 CLI。"""

from techradar.cli import main

if __name__ == "__main__":
    raise SystemExit(main())