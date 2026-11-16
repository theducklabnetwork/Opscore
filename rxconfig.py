import os

import reflex as rx

config = rx.Config(
    app_name="ducklab",
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
        rx.plugins.RadixThemesPlugin(
            theme=rx.theme(
                accent_color="blue",
                gray_color="slate",
                radius="medium",
                appearance="light",
            )
        ),
    ],
    show_built_with_reflex=False,
    **(
        {"backend_port": 47811, "api_url": "http://127.0.0.1:47811"}
        if os.environ.get("OPSCORE_DESKTOP") == "1"
        else {}
    ),
)
