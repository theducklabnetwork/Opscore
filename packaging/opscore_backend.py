"""Start the prebuilt Opscore app on a loopback-only port."""

import argparse
import os
from pathlib import Path

os.environ.update(
    REFLEX_SKIP_COMPILE="true",
    REFLEX_ENV_MODE="prod",
    REFLEX_CHECK_LATEST_VERSION="false",
    REFLEX_MOUNT_FRONTEND_COMPILED_APP="true",
    DUCKLAB_SECURE_COOKIES="false",
)

APP_ROOT = Path(__file__).resolve().parent.parent
os.chdir(APP_ROOT)

from reflex.utils.exec import run_backend_prod  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the packaged Opscore backend")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if not 0 < args.port < 65536:
        parser.error("--port must be between 1 and 65535")
    from reflex_base.constants.base import LogLevel

    run_backend_prod(
        "127.0.0.1", args.port, LogLevel.INFO, mount_frontend_compiled_app=True
    )


if __name__ == "__main__":
    main()
