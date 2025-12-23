from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright
import logging
import sys
from contextlib import asynccontextmanager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr 
)

logger = logging.getLogger("BrowserAgent")



browser_state = {
    "playwright": None,
    "browser": None,
    "page": None
}

@asynccontextmanager
async def browser_lifespan(server: FastMCP):
    """Handles browser setup and teardown."""
    logger.info("Starting browser...")
    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=False)
    page = await browser.new_page()
    logger.info("Browser started.")

    # Save to global state
    browser_state["playwright"] = p
    browser_state["browser"] = browser
    browser_state["page"] = page
    
    try:
        yield # The server runs while this is suspended
    finally:
        # Cleanup
        logger.info("Closing browser...")
        await browser.close()
        await p.stop()
        logger.info("Browser closed.")

mcp = FastMCP("BrowserAgent", lifespan=browser_lifespan)

# mcp = FastMCP("BrowserAgent")

# @mcp.on_startup()
# async def startup():
#     logger.info("Starting up BrowserAgent...")

#     playwright = await async_playwright().start()
#     browser = await playwright.chromium.launch(headless=False)
#     page = await browser.new_page()
    
#     browser_state["playwright"] = playwright
#     browser_state["browser"] = browser
#     browser_state["page"] = page
#     logger.info("BrowserAgent started.")

# @mcp.on_shutdown()
# async def shutdown():
#     logger.info("Shutting down BrowserAgent...")
#     if browser_state["browser"]:
#         await browser_state["browser"].close()
#     if browser_state["playwright"]:
#         await browser_state["playwright"].stop()


@mcp.tool()
async def visit_url(url: str) -> str:
    """Visit a URL and return the page title."""
    page = browser_state["page"]
    if not page:
        raise RuntimeError("Browser is not initialized.")

    logger.info(f"Visiting URL: {url}")
    await page.goto(url)
    title = await page.title()
    logger.info(f"Visited {url}, title: {title}")
    return title

if __name__ == "__main__":
    mcp.run()