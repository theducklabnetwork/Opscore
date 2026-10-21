import reflex as rx

config = rx.Config(
    app_name="ducklab",
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
        rx.plugins.RadixThemesPlugin(
            theme=rx.theme(
                accent_color="indigo",
                gray_color="slate",
                radius="large",
                appearance="dark",
            )
        ),
    ]
)