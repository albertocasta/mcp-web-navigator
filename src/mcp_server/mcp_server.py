from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright
import logging
from contextlib import asynccontextmanager
import json
import sys
from pathlib import Path

# aggiunge src al sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from helper import clean_html_content

# Configure logging
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


@mcp.tool()
async def visit_url(url: str) -> str:
    """
    Visit a URL and return the page title.

    This function will block until the page has finished loading.
    """
    page = browser_state["page"]
    if not page:
        raise RuntimeError("Browser is not initialized.")

    # Log the URL we are visiting
    logger.info(f"Visiting URL: {url}")

    # Navigate to the URL
    await page.goto(url)

    # Get the title of the page
    title = await page.title()

    # Log the title of the page
    logger.info(f"Visited {url}, title: {title}")

    # Return the title
    return title

@mcp.tool()
async def get_page_content() -> str:
    """
    Get the cleaned HTML content of the current page.
    """
    page = browser_state["page"]
    if not page:
        raise RuntimeError("Browser is not initialized.")

    # Get the raw HTML content
    raw_html = await page.content()

    # Clean the HTML content
    cleaned_html = clean_html_content(raw_html)

    # Log the length of the cleaned content
    logger.info(f"Retrieved and cleaned page content, length: {len(cleaned_html)} characters")

    return cleaned_html

@mcp.tool()
async def fill_text(selector: str, text: str) -> None:
    """
    Fill a text input identified by the selector with the given text.
    """
    page = browser_state["page"]
    if not page:
        raise RuntimeError("Browser is not initialized.")

    if not selector or not text:
        raise ValueError("Selector and text must be provided.")
    
    try:
        # Log the action
        logger.info(f"Filling text in selector '{selector}' with text '{text}'")
        # Fill the text input
        await page.fill(selector, text)
        logger.info(f"Filled text in selector '{selector}'")

    except Exception as e:
        logger.error(f"Error filling text in selector '{selector}': {e}")
        raise e

@mcp.tool()
async def click_element(selector: str) -> None:
    """
    Click an element identified by the selector.
    """
    page = browser_state["page"]
    if not page:
        raise RuntimeError("Browser is not initialized.")

    if not selector:
        raise ValueError("Selector must be provided.")

    try:
        await page.click(selector)
        logger.info(f"Clicked element with selector '{selector}'")
    except Exception as e:
        logger.error(f"Error clicking element with selector '{selector}': {e}")
        raise e

@mcp.tool()
async def click_by_text(text: str, exact_match: bool = False) -> str:
    """
    Click an element by its visible text content.
    
    Args:
        text: The visible text to search for (e.g., "Login", "Submit", "Home")
        exact_match: If True, requires exact text match. If False (default), matches partial text.
    
    Returns:
        A message confirming which element was clicked.
    
    Examples:
        - click_by_text("Login") - clicks the first element containing "Login"
        - click_by_text("Submit", exact_match=True) - clicks element with exactly "Submit"
    """
    page = browser_state["page"]
    if not page:
        raise RuntimeError("Browser is not initialized.")

    if not text:
        raise ValueError("Text must be provided.")

    try:
        logger.info(f"Searching for element with text '{text}' (exact_match={exact_match})")
        
        # Execute JavaScript to find and click the element
        result = await page.evaluate("""({text, exactMatch}) => {
            const query = 'button, a, input[type="submit"], input[type="button"], [role="button"], [onclick]';
            const elements = Array.from(document.querySelectorAll(query));
            
            // Filter visible elements
            const visibleElements = elements.filter(el => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && 
                       style.visibility !== 'hidden' && style.display !== 'none';
            });
            
            // Find element by text
            let targetElement = null;
            for (const el of visibleElements) {
                const elementText = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
                
                if (exactMatch) {
                    if (elementText === text) {
                        targetElement = el;
                        break;
                    }
                } else {
                    if (elementText.toLowerCase().includes(text.toLowerCase())) {
                        targetElement = el;
                        break;
                    }
                }
            }
            
            if (!targetElement) {
                return { success: false, message: `No element found with text "${text}"` };
            }
            
            // Get element info before clicking
            const elementInfo = {
                tag: targetElement.tagName.toLowerCase(),
                text: (targetElement.innerText || targetElement.value || '').substring(0, 100),
                id: targetElement.id,
                className: targetElement.className
            };
            
            // Click the element
            targetElement.click();
            
            return { 
                success: true, 
                message: `Clicked ${elementInfo.tag} with text "${elementInfo.text}"`,
                element: elementInfo
            };
        }""", {"text": text, "exactMatch": exact_match})
        
        if result["success"]:
            logger.info(result["message"])
            return result["message"]
        else:
            logger.error(result["message"])
            raise RuntimeError(result["message"])
            
    except Exception as e:
        logger.error(f"Error clicking element with text '{text}': {e}")
        raise e

@mcp.tool()
async def get_interactive_elements() -> str:
    """
    Scans the current page to find interactive elements (buttons, links, inputs).
    Returns a JSON list with name (visible text), selector (CSS selector), and link (URL if applicable).
    Useful for understanding what elements are available to interact with.
    """
    page = browser_state["page"]
    if not page:
        raise RuntimeError("Browser is not initialized.")

    # Execute JavaScript in the browser to extract elements
    elements = await page.evaluate("""() => {
        const items = [];
        // Query for commonly interactive elements
        const query = 'button, a, input, select, textarea, [role="button"], [onclick]';
        
        document.querySelectorAll(query).forEach((el) => {
            // 1. Filter out invisible elements (zero size or hidden style)
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            if (rect.width < 1 || rect.height < 1 || style.visibility === 'hidden' || style.display === 'none') return;
            
            // 2. Build a "best guess" CSS selector
            let selector = el.tagName.toLowerCase();
            if (el.id) {
                selector += `#${el.id}`;
            } else if (el.className && typeof el.className === 'string' && el.className.trim() !== '') {
                // Use the first valid class to keep the selector concise
                selector += `.${el.className.trim().split(/\s+/)[0]}`;
            }
            
            // 3. Extract meaningful text (name - what the user sees)
            let name = el.innerText || el.placeholder || el.value || el.getAttribute('aria-label') || "";
            // Clean up whitespace and truncate long text
            name = name.replace(/\\s+/g, ' ').trim().substring(0, 100);

            // Ignore empty elements unless they are form inputs
            if (!name && !['input', 'select', 'textarea'].includes(el.tagName.toLowerCase())) return;

            // 4. Extract link (URL) if it's a link or button with href
            let link = null;
            if (el.tagName.toLowerCase() === 'a' && el.href) {
                link = el.href;
            } else if (el.getAttribute('href')) {
                link = el.getAttribute('href');
            }

            items.push({
                name: name,
                selector: selector,
                link: link
            });
        });
        return items;
    }""")
    
    logger.info(f"Found {len(elements)} interactive elements on the page")
    return json.dumps(elements, indent=2)

if __name__ == "__main__":
    mcp.run()