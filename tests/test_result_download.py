import io,os,zipfile
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

@pytest.fixture
def browser_page():
 with sync_playwright() as p:
  browser=p.chromium.launch(**({'executable_path':os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE']} if os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') else {}))
  page=browser.new_page()
  page.route('https://download-test.local/',lambda route:route.fulfill(content_type='text/html',body='<p id="progressStep">Results ready</p><a id="downloadBtn" href="/download/sample">Download Results</a>'))
  page.goto('https://download-test.local/')
  page.add_script_tag(content=Path('static/download_results.js').read_text())
  yield page
  browser.close()

def payload():
 raw=io.BytesIO()
 with zipfile.ZipFile(raw,'w') as archive:archive.writestr('result.txt','test results')
 return raw.getvalue()

def test_interrupted_download_retries_without_leaving_page(browser_page,tmp_path):
 page=browser_page;attempts=[];content=payload()
 def respond(route):
  attempts.append(1)
  if len(attempts)==1:route.abort('connectionclosed')
  else:route.fulfill(content_type='application/zip',body=content)
 page.route('**/download/sample',respond)
 with page.expect_download() as saved:
  page.locator('#downloadBtn').click()
 file=tmp_path/'results.zip';saved.value.save_as(file)
 assert file.read_bytes()==content
 assert len(attempts)==2
 assert page.url=='https://download-test.local/'
 assert page.locator('#downloadBtn').inner_text()=='Download Results'
 assert 'Download started' in page.locator('#progressStep').inner_text()

def test_expired_results_show_error_on_same_page(browser_page):
 page=browser_page;attempts=[]
 def respond(route):
  attempts.append(1);route.fulfill(status=404,json={'error':'Results not found'})
 page.route('**/download/sample',respond)
 page.locator('#downloadBtn').click()
 page.wait_for_function("document.querySelector('#progressStep').textContent.includes('Generate the results again')")
 assert len(attempts)==1
 assert page.url=='https://download-test.local/'
 assert page.locator('#downloadBtn').get_attribute('aria-disabled') is None

def test_incomplete_zip_is_retried(browser_page,tmp_path):
 page=browser_page;attempts=[];content=payload()
 def respond(route):
  attempts.append(1);route.fulfill(content_type='application/zip',body=b'partial' if len(attempts)==1 else content)
 page.route('**/download/sample',respond)
 with page.expect_download() as saved:page.locator('#downloadBtn').click()
 file=tmp_path/'results.zip';saved.value.save_as(file)
 assert file.read_bytes()==content and len(attempts)==2
