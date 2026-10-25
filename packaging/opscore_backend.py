"""Start the prebuilt Opscore app on a loopback-only port."""

import argparse
import os
from pathlib import Path

os.environ.pop("REFLEX_SKIP_COMPILE", None)
os.environ.update(
    REFLEX_ENV_MODE="prod",
    REFLEX_CHECK_LATEST_VERSION="false",
    REFLEX_MOUNT_FRONTEND_COMPILED_APP="true",
    DUCKLAB_SECURE_COOKIES="false",
)

APP_ROOT = Path(__file__).resolve().parent.parent
os.chdir(APP_ROOT)
# Reflex deletes its marker before importing the app. Override only that
# lookup, preserving the absolute read-only frontend bundle in /opt/opscore.
from reflex_base import constants  # noqa: E402
from reflex.utils import prerequisites  # noqa: E402

packaged_web = prerequisites.get_web_dir()
if os.environ.get("DUCKLAB_DB_PATH"):
    runtime_web = Path(os.environ["DUCKLAB_DB_PATH"]).parent / "reflex-web"
    runtime_web.mkdir(parents=True, exist_ok=True)
    (runtime_web / constants.NOCOMPILE_FILE).touch()
    original_get_web_dir = prerequisites.get_web_dir

    def get_web_dir_with_runtime_marker():
        return runtime_web

    prerequisites.get_web_dir = get_web_dir_with_runtime_marker
    import reflex.utils.exec as reflex_exec

    original_get_frontend_mount = reflex_exec.get_frontend_mount

    def get_packaged_frontend_mount(router=None):
        prerequisites.get_web_dir = lambda: packaged_web
        try:
            return original_get_frontend_mount(router)
        finally:
            prerequisites.get_web_dir = get_web_dir_with_runtime_marker

    reflex_exec.get_frontend_mount = get_packaged_frontend_mount

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
