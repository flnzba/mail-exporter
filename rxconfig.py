import reflex as rx

config = rx.Config(
    app_name="mailexport",
    # Change port here if 3000 is already in use
    frontend_port=3000,
    backend_port=8000,
    plugins=[
        # Theme moved here from App(theme=...), which is deprecated in 0.9.x.
        rx.plugins.RadixThemesPlugin(
            theme=rx.theme(
                appearance="light",
                accent_color="blue",
                radius="medium",
                scaling="95%",
            ),
        ),
        rx.plugins.SitemapPlugin(),
    ],
)
