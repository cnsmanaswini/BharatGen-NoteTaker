"""Chromium launch options."""
from app.config.settings import Settings


def build_launch_kwargs(settings: Settings) -> dict:
    kwargs: dict = {
        "user_data_dir": str(settings.user_data_dir),
        "headless": settings.headless,
        "viewport": {"width": 1280, "height": 800},
        # Drop the automation flag; it triggers Google's "browser not secure" block.
        "ignore_default_args": ["--enable-automation"],
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if settings.browser_channel:
        kwargs["channel"] = settings.browser_channel
    return kwargs
