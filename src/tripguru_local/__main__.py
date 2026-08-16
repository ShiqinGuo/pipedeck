import uvicorn

from tripguru_local.api import app
from tripguru_local.settings import LocalSettings


def main() -> None:
    settings = LocalSettings()
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
