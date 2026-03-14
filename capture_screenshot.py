"""Capture a screenshot of the Architect graph viewer for UI verification."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

def main():
    output_path = Path(__file__).parent / "architect_viewer_screenshot.png"
    url = "http://localhost:8742"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True,
        )
        page = context.new_page()

        # Collect console messages
        console_logs = []
        page.on("console", lambda msg: console_logs.append({
            "type": msg.type,
            "text": msg.text,
        }))

        try:
            page.goto(url, wait_until="networkidle", timeout=15000)
            # Wait for graph to initialize and layout to settle (Cytoscape force layout)
            page.wait_for_timeout(3500)
            page.screenshot(path=str(output_path), full_page=True)
            print(f"Screenshot saved to: {output_path}")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            browser.close()

        # Report console errors
        errors = [log for log in console_logs if log["type"] == "error"]
        if errors:
            print("\nConsole errors:")
            for err in errors:
                print(f"  - {err['text']}")

if __name__ == "__main__":
    main()
