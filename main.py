"""Entry point: run the website_to_okf CLI.

Usage:
    python main.py https://example.com -o ./bundle --no-llm
"""

import sys

from website_to_okf.cli import main

if __name__ == "__main__":
    sys.exit(main())
