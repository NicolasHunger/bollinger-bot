"""Ermöglicht `python -m bollinger_bot`: startet den Bot, der Exit-Code kommt von main()."""

import sys

from bollinger_bot.start import main

if __name__ == "__main__":
    sys.exit(main())
