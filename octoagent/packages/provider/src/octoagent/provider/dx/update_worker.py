"""Feature 024 detached update worker。"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .update_service import UpdateService


async def _run(project_root: Path, attempt_id: str) -> None:
    service = UpdateService(project_root)
    await service.execute_attempt(attempt_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="OctoAgent detached update worker")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    asyncio.run(_run(Path(args.project_root), args.attempt_id))


if __name__ == "__main__":
    main()
