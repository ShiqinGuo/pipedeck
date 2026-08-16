from unittest.mock import patch

from tripguru_local.__main__ import main
from tripguru_local.api import app


def test_main_passes_the_imported_application_to_uvicorn() -> None:
    with patch("tripguru_local.__main__.uvicorn.run") as run:
        main()

    run.assert_called_once_with(
        app,
        host="127.0.0.1",
        port=7421,
        log_level="info",
    )
