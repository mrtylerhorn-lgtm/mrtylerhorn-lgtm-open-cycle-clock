"""Render the built page in headless Chromium: screenshots + console errors."""
import sys, glob
from playwright.sync_api import sync_playwright
html, outdir = sys.argv[1], sys.argv[2]
exe = glob.glob('/opt/pw-browsers/chromium-*/chrome-linux64/chrome')[0]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=exe)
    for name, w, scheme in [('desk-light', 1280, 'light'), ('desk-dark', 1280, 'dark'), ('phone', 390, 'light')]:
        pg = b.new_page(viewport={'width': w, 'height': 900}, color_scheme=scheme, device_scale_factor=1)
        errs = []
        pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.goto('file://' + html); pg.wait_for_timeout(800)
        pg.screenshot(path=f'{outdir}/{name}.png', full_page=True)
        sw = pg.evaluate('document.documentElement.scrollWidth')
        print(name, 'scrollWidth', sw, 'errors', errs)
    b.close()
