import sys
from playwright.sync_api import sync_playwright

URL = "https://uarb.novascotia.ca/fmi/webd/UARB15"

DUMP_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
    if (!own) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.y > 330) continue;
    out.push(`${Math.round(r.x)},${Math.round(r.y)} ${el.tagName}.${el.className} :: ${own.slice(0, 90)}`);
  }
  return out;
}
"""


def search(page, matter):
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.get_by_text("Go Directly to Matter").wait_for(timeout=30000)
    bb = page.get_by_text("eg M01234").bounding_box()
    page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
    page.wait_for_function("document.activeElement && document.activeElement.isContentEditable", timeout=10000)
    page.keyboard.type(matter)
    box_y = page.get_by_text("Go Directly to Matter").bounding_box()["y"]
    btn = min((b for b in page.get_by_text("Search", exact=True).all() if b.bounding_box()["y"] > box_y),
              key=lambda b: b.bounding_box()["y"])
    tb = btn.bounding_box()
    page.mouse.click(tb["x"] + tb["width"] / 2, tb["y"] + tb["height"] / 2)
    page.wait_for_timeout(6000)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1400, "height": 1000})
    search(page, sys.argv[1] if len(sys.argv) > 1 else "M12205")
    print("\n".join(l for l in page.evaluate(DUMP_JS) if "DIV.text" in l))
    print("\n===== INVALID MATTER =====")
    page = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
    search(page, "M99999")
    page.screenshot(path="recon_out/invalid.png")
    print(page.inner_text("body")[:1500])
    browser.close()
