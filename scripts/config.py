"""Print the effective settings and where each came from. With --check, only validate."""

import sys

import settings

if "--check" in sys.argv[1:]:
    try:
        settings.check()
    except ValueError as error:
        sys.exit(str(error))
else:
    for key, (value, source) in settings.effective().items():
        print(f"  {key:16} {str(value):20} [{source}]")
