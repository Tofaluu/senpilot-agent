import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "https://uarb.novascotia.ca/fmi/webd/UARB15"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "recon_out")
MATTER = sys.argv[2] if len(sys.argv) > 2 else "M12205"
OUT.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    clicked = {"v": False}
    # The socket opens on page load, so the listener must be attached before goto.
    page.on("websocket", lambda ws: ws.on("framereceived", lambda f: clicked["v"] and print("WS FRAME:", str(f)[:800])))
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.get_by_text("Go Directly to Matter").wait_for(timeout=30000)

    # The input is a placeholder div until clicked; FileMaker swaps in a real <input> on focus.
    # An overlay div intercepts pointer events, so click by coordinates like a real user.
    bb = page.get_by_text("eg M01234").bounding_box()
    page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
    page.wait_for_timeout(500)
    page.keyboard.type(MATTER)
    print("Focused element after click:", page.evaluate("document.activeElement.outerHTML.slice(0,200)"))

    # Pick the Search button closest (vertically) to the matter box.
    box_y = page.get_by_text("Go Directly to Matter").bounding_box()["y"]
    buttons = page.get_by_text("Search", exact=True).all()
    target = min(
        (b for b in buttons if b.bounding_box()["y"] > box_y),
        key=lambda b: b.bounding_box()["y"],
    )
    tb = target.bounding_box()
    page.mouse.click(tb["x"] + tb["width"] / 2, tb["y"] + tb["height"] / 2)

    page.get_by_text("Other Documents -").wait_for(timeout=30000)
    page.get_by_text("Other Documents -").click(force=True)
    page.get_by_text("GO GET IT").first.wait_for(timeout=30000)
    page.wait_for_timeout(1500)
    page.screenshot(path=OUT / "tab.png", full_page=True)
    print("\n--- TAB TEXT ---")
    print(page.inner_text("body")[:2500])

    ctx = page.context
    ctx.on("page", lambda pg: print("NEW TAB OPENED:", pg.url))
    page.on("request", lambda r: r.resource_type not in ("image", "font", "stylesheet") and print("REQ:", r.method, r.url[:200]))
    page.on("download", lambda d: print("DOWNLOAD:", d.suggested_filename, d.url[:200]))

    def click_center(loc):
        b = loc.bounding_box()
        page.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)

    dl_dir = OUT / "downloads_test"
    dl_dir.mkdir(exist_ok=True)
    for i in range(2):
        gg = page.get_by_text("GO GET IT").nth(i)
        gg.scroll_into_view_if_needed()
        click_center(gg)
        btn = page.locator(".fm-download-button").first
        btn.wait_for(timeout=30000)
        with page.expect_download(timeout=60000) as info:
            btn.click()
        d = info.value
        d.save_as(dl_dir / d.suggested_filename)
        print(f"SAVED #{i}:", d.suggested_filename, (dl_dir / d.suggested_filename).stat().st_size, "bytes")
        page.get_by_text("Close", exact=True).click()
        btn.wait_for(state="detached", timeout=15000)
    for pg in ctx.pages:
        print("OPEN PAGE:", pg.url[:200])
    browser.close()
