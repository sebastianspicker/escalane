"""Browser-level accessibility and layout checks against the served operator console."""

from __future__ import annotations

import uuid

import httpx
import pytest

from escalane.db.models import Alarm
from tests.constants import TEST_DEVICE_TOKEN
from tests.e2e.test_http_service_e2e import (
    ADMIN_KEY,
    SEED_FILE,
    ServedApp,
)

playwright = pytest.importorskip("playwright.async_api")

pytestmark = pytest.mark.e2e
pytest_plugins = ("tests.e2e.test_http_service_e2e",)


async def _create_triggered_alarm(served_app: ServedApp) -> str:
    """Seed and trigger an alarm, returning its private ACK token for browser coverage."""
    async with httpx.AsyncClient(base_url=served_app.base_url) as api:
        seed = await api.post(
            "/v1/admin/seed",
            headers={"X-Admin-Key": ADMIN_KEY, "Content-Type": "application/x-yaml"},
            content=SEED_FILE.read_bytes(),
        )
        assert seed.status_code == 200
        trigger = await api.get("/v1/yealink/alarm", params={"token": TEST_DEVICE_TOKEN})
        assert trigger.status_code == 200
        alarm_id = uuid.UUID(trigger.json()["alarm_id"])
    async with served_app.sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        assert alarm is not None and alarm.ack_token
        return alarm.ack_token


def _record_browser_errors(page, served_app: ServedApp) -> tuple[list[str], list[str]]:
    """Collect browser-console failures and accidental outbound resource requests."""
    console_errors: list[str] = []
    external_requests: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )
    page.on(
        "request",
        lambda request: (
            external_requests.append(request.url)
            if not request.url.startswith(served_app.base_url)
            else None
        ),
    )
    return console_errors, external_requests


async def _exercise_operator_console(page, served_app: ServedApp, ack_token: str) -> None:
    """Traverse the critical operator and responder flows with keyboard assertions."""
    await page.goto(f"{served_app.base_url}/admin/login?lang=en")
    await page.get_by_label("Operator name").fill("Browser Ops")
    await page.get_by_label("Admin key").fill(ADMIN_KEY)
    await page.get_by_role("button", name="Sign in").click()
    await page.get_by_role("heading", name="Alarm worklist").wait_for()
    assert await page.get_by_role("button", name="Sign out").is_visible()
    await page.get_by_role("link", name="Open alarm", exact=False).first.click()
    # Detail hero titles the person · room context (Route Ledger), not a generic page label.
    await page.locator("main h1").wait_for()
    assert await page.get_by_role("button", name="Acknowledge alarm").is_visible()
    resolve_button = page.get_by_role("button", name="Resolve alarm")
    await resolve_button.click()
    dialog = page.get_by_role("dialog", name="Resolve alarm")
    await dialog.wait_for()
    await dialog.get_by_role("button", name="Keep alarm open").click()
    assert await resolve_button.evaluate("element => element === document.activeElement")

    await page.goto(f"{served_app.base_url}/a/{ack_token}?lang=en")
    await page.get_by_label("Your name").fill("Browser Responder")
    await page.get_by_label("Note").fill("Responding")
    await page.get_by_role("button", name="Acknowledge alarm").click()
    await page.get_by_text("Acknowledged", exact=True).wait_for()
    await page.keyboard.press("Tab")
    assert await page.locator("body").evaluate(
        "element => element.scrollWidth === element.clientWidth"
    )

    confirmation_messages: list[str] = []

    async def dismiss_confirmation(dialog) -> None:
        confirmation_messages.append(dialog.message)
        await dialog.dismiss()

    await page.goto(f"{served_app.base_url}/admin/simulation?lang=en")
    page.once("dialog", dismiss_confirmation)
    await page.get_by_role("button", name="Clear notifications").click()
    assert confirmation_messages == ["Clear all simulated notification records?"]
    assert await page.get_by_role("heading", name="Simulation").is_visible()


@pytest.mark.parametrize("browser_name", ["chromium", "firefox", "webkit"])
async def test_operator_console_csp_keyboard_and_narrow_viewport(
    served_app: ServedApp, browser_name: str
) -> None:
    ack_token = await _create_triggered_alarm(served_app)
    async with playwright.async_playwright() as manager:
        browser = await getattr(manager, browser_name).launch()
        page = await browser.new_page(viewport={"width": 320, "height": 568})
        console_errors, external_requests = _record_browser_errors(page, served_app)
        await _exercise_operator_console(page, served_app, ack_token)
        assert not console_errors
        assert not external_requests
        await browser.close()
