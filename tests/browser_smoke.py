"""Run against a fresh local server: .venv/bin/python tests/browser_smoke.py."""
from pathlib import Path
import os
from playwright.sync_api import sync_playwright

BASE = os.environ.get('BASE_URL', 'http://127.0.0.1:8000')
OUT = Path('test-results'); OUT.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
    page = browser.new_page(viewport={'width':1440,'height':1080})
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(BASE)
    page.get_by_role('heading',name='A clear path to approval.').wait_for()
    page.locator('#new-submission').click()
    for label,status in [('Complete submission','approved'),('Missing & expired evidence','pending'),('Third-party payee','pending'),('Invalid routing checksum','pending'),('Shared account','rejected')]:
        page.get_by_role('button',name=label+' ↗',exact=False).click()
        page.get_by_role('button',name='Run onboarding checks').click()
        page.locator('#run-status').filter(has_text=status).wait_for()
        assert page.locator('.stage.done').count()==6
        page.locator('#result .communication').wait_for()
        with page.expect_download() as d:
            page.get_by_role('button',name='Decision report',exact=False).click()
        d.value.save_as(OUT/d.value.suggested_filename)
        page.get_by_role('button',name='← All submissions').click()
        if label!='Shared account':
            page.locator('#new-submission').click()
    page.screenshot(path=str(OUT/'dashboard.png'),full_page=True)
    assert page.locator('#metric-approved').inner_text()=='1'
    assert page.locator('#metric-pending').inner_text()=='3'
    assert page.locator('#metric-rejected').inner_text()=='1'
    page.locator('#status-filter').select_option('rejected')
    assert page.locator('#activity tr').count()==1
    page.get_by_role('button',name='View Cedar Logistics LLC').click()
    page.get_by_role('heading',name='This submission needs escalation.').wait_for()
    page.screenshot(path=str(OUT/'rejected.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    page.get_by_role('button',name='← All submissions').click()
    page.locator('.nav[data-view="submission"]').click()
    assert page.locator('#submission-view').is_visible()
    page.screenshot(path=str(OUT/'mobile.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert not errors,errors
    browser.close()
    print('PASS: five live scenarios, reports, dashboard counts, status filter, history detail, mobile layout; no browser errors.')
