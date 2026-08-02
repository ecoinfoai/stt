"""``python -m stt``로도 CLI를 실행할 수 있게 하는 진입점."""
from __future__ import annotations

from stt.cli import main

raise SystemExit(main())
