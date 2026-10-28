import sys
from playwright.sync_api import sync_playwright
import scraper

matter, doc_type = sys.argv[1], sys.argv[2]
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_context(viewport={"width": 1400, "height": 2400}).new_page()
    scraper._search(page, matter)
    scraper._click(page, page.get_by_text(f"{doc_type} -").first.bounding_box())
    page.get_by_text("GO GET IT").first.wait_for(timeout=30000)
    page.wait_for_timeout(1000)
    page.screenshot(path="recon_out/tab2.png")
    for n in page.evaluate(scraper.TEXT_NODES_JS):
        if 250 < n["y"] < 700:
            print(round(n["x"]), round(n["y"]), n["cls"][:30], "::", n["text"][:70])
    browser.close()
