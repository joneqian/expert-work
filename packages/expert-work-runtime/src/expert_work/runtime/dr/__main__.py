"""``python -m expert_work.runtime.dr`` → invoke the CLI."""

import sys

from expert_work.runtime.dr.cli import main

if __name__ == "__main__":
    sys.exit(main())
