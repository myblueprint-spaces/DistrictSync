"""Playwright headless Chrome smoke tests for the Streamlit UI.

Verifies that each page of the multi-page Streamlit app loads without
crashing and renders key structural elements. These tests do NOT click
through full workflows — they're smoke tests to catch broken imports,
missing files, or rendering errors in CI.

Requirements:
    pip install pytest-playwright requests
    playwright install chromium

The Streamlit server is started once per test session via the
`streamlit_server` fixture in conftest.py (port 8502, headless).
"""

import pytest

pytest.importorskip("playwright", reason="pytest-playwright not installed — skipping UI smoke tests")
pytest.importorskip("requests", reason="requests not installed — skipping UI smoke tests")

pytestmark = pytest.mark.ui

# Playwright fixtures (`page`, `playwright`) are provided by pytest-playwright.
# The `streamlit_server` session fixture is in conftest.py.

_APP_CONTAINER = "[data-testid='stApp']"
_TIMEOUT = 30_000  # 30 seconds — Streamlit can be slow on first render
# Streamlit renders content via WebSocket after the HTML shell loads.
# We wait for the shell, then give the WebSocket render a short time to settle.
_RENDER_SETTLE_MS = 3_000


def _wait_for_page(page, url: str) -> None:
    """Navigate to URL and wait until Streamlit's content is rendered.

    Streamlit pushes content via WebSocket after the shell loads, so we
    wait for the shell then give it a brief settle period. We do NOT use
    networkidle because Streamlit's persistent WebSocket prevents it.
    """
    page.goto(url, timeout=_TIMEOUT)
    page.wait_for_selector(_APP_CONTAINER, timeout=_TIMEOUT)
    page.wait_for_timeout(_RENDER_SETTLE_MS)


class TestHomePageSmoke:
    def test_home_loads_without_error(self, page, streamlit_server):
        """Home page renders the main app container without a crash."""
        _wait_for_page(page, streamlit_server)

    def test_home_shows_app_title(self, page, streamlit_server):
        """The GDE2Acsv app name must appear somewhere on the home page."""
        _wait_for_page(page, streamlit_server)
        # Look for the app title in the DOM — any element containing "GDE2Acsv"
        assert page.locator("text=GDE2Acsv").count() > 0, (
            "GDE2Acsv title not found anywhere on home page"
        )

    def test_home_has_navigation_sidebar(self, page, streamlit_server):
        """The sidebar navigation must be present."""
        _wait_for_page(page, streamlit_server)
        assert page.locator("[data-testid='stSidebar']").count() > 0, (
            "Sidebar not found on home page"
        )


class TestSetupWizardSmoke:
    def test_setup_wizard_loads(self, page, streamlit_server):
        """Setup Wizard page renders without crashing."""
        _wait_for_page(page, f"{streamlit_server}/Setup_Wizard")

    def test_setup_wizard_has_content(self, page, streamlit_server):
        """Setup Wizard shows either the wizard steps or management view."""
        _wait_for_page(page, f"{streamlit_server}/Setup_Wizard")
        # At minimum, some rendered content must exist (heading or markdown block)
        has_heading = page.locator("h1, h2, h3").count() > 0
        has_markdown = page.locator("[data-testid='stMarkdown']").count() > 0
        assert has_heading or has_markdown, (
            "Neither heading nor markdown content found on Setup Wizard page"
        )


class TestConvertPageSmoke:
    def test_convert_page_loads(self, page, streamlit_server):
        """Convert page renders without crashing."""
        _wait_for_page(page, f"{streamlit_server}/Convert")

    def test_convert_has_file_uploader(self, page, streamlit_server):
        """The GDE file uploader widget must appear on the Convert page."""
        _wait_for_page(page, f"{streamlit_server}/Convert")
        assert page.locator("[data-testid='stFileUploader']").count() > 0, (
            "File uploader widget not found on Convert page"
        )

    def test_convert_has_district_selectbox(self, page, streamlit_server):
        """At least one selectbox (district picker) must be present."""
        _wait_for_page(page, f"{streamlit_server}/Convert")
        assert page.locator("[data-testid='stSelectbox']").count() > 0, (
            "No selectbox found on Convert page"
        )


class TestRunHistorySmoke:
    def test_run_history_loads(self, page, streamlit_server):
        """Run History page renders without crashing."""
        _wait_for_page(page, f"{streamlit_server}/Run_History")

    def test_run_history_shows_content(self, page, streamlit_server):
        """Either run log table or 'no history' message must appear."""
        _wait_for_page(page, f"{streamlit_server}/Run_History")
        # Accept any rendered content — markdown, dataframe, or info box
        has_content = page.locator(
            "[data-testid='stMarkdown'], [data-testid='stDataFrame'], [data-testid='stAlert']"
        ).count() > 0
        assert has_content, "Run History page has no rendered content"


class TestHelpPageSmoke:
    def test_help_page_loads(self, page, streamlit_server):
        """Help page renders without crashing."""
        _wait_for_page(page, f"{streamlit_server}/Help")

    def test_help_has_tabs(self, page, streamlit_server):
        """The documentation tab bar must be visible."""
        _wait_for_page(page, f"{streamlit_server}/Help")
        assert page.locator("[data-testid='stTabs']").count() > 0, (
            "Tab bar not found on Help page"
        )

    def test_help_installation_tab_has_content(self, page, streamlit_server):
        """Clicking the Installation tab renders markdown content."""
        _wait_for_page(page, f"{streamlit_server}/Help")
        tab = page.get_by_role("tab", name="Installation")
        if tab.count() > 0:
            tab.first.click()
            page.wait_for_timeout(2_000)  # Let Streamlit re-render the tab
            # Markdown elements exist (may include hidden tabs — count > 0 is sufficient)
            assert page.locator("[data-testid='stMarkdown']").count() > 0, (
                "No markdown content found after clicking Installation tab"
            )
