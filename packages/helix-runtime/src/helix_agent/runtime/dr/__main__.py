"""``python -m helix_agent.runtime.dr`` → invoke the CLI."""

import sys

from helix_agent.runtime.dr.cli import main

if __name__ == "__main__":
    sys.exit(main())
