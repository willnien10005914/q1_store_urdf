// Static-hosting workflow verification. Run against dist served by an existing HTTP server.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.Q1_BASE_URL||'http://127.0.0.1:8892';
const report={base,pageErrors:[]};
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
try{
 const context=await browser.newContext({viewport:{width:1280,height:900},acceptDownloads:true});
 const page=await context.newPage();page.on('pageerror',e=>report.pageErrors.push(e.message));
 await page.goto(base,{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>document.querySelector('#studio-viewer').dataset.modelReady==='true',null,{timeout:30000});
 await page.locator('#load-example').click();
 await page.waitForFunction(()=>document.querySelector('#people button'),null,{timeout:30000});
 report.runtime=await page.locator('#runtime-badge').textContent();assert.match(report.runtime,/瀏覽器/);
 await page.locator('[data-panel="world"]').click();
 await page.locator('[name="run-mode"][value="demo"]').check();
 await page.locator('#start-job').click();
 await page.waitForFunction(()=>document.querySelector('#job-state').textContent.includes('完成'),null,{timeout:45000});
 report.state=await page.locator('#job-state').textContent();
 report.progress=await page.locator('#job-percent').textContent();assert.equal(report.progress,'100%');
 report.video=await page.locator('#result-video').getAttribute('src');assert.match(report.video,/assets\/examples\/leg/);
 report.provenance=await page.locator('#result-provenance').textContent();
 await page.locator('#share-job').click();
 await page.locator('#listing-title').fill('Static QA · Kung Fu');
 await page.locator('#listing-price').fill('12.50');
 await page.locator('#share-form [type="submit"]').click();
 await page.waitForFunction(()=>document.querySelectorAll('#community-cards article').length===1,null,{timeout:10000});
 await page.locator('#community-cards button').filter({hasText:'模擬購買'}).click();
 await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('未收款'),null,{timeout:10000});
 report.purchaseToast=await page.locator('#toast').textContent();
 report.storage=await page.evaluate(()=>{const d=JSON.parse(localStorage.getItem('q1-store-v1'));return {job_count:Object.keys(d.jobs).length,motion_count:Object.keys(d.motions).length,listing_count:d.listings.length,purchases:d.purchases,job:Object.values(d.jobs)[0],listing:d.listings[0]};});
 assert.equal(report.storage.listing_count,1);assert.equal(report.storage.purchases.length,1);assert.equal(report.storage.purchases[0].charged,false);assert.equal(report.storage.job.result.newly_trained,false);assert.equal(report.storage.job.result.landmark_rendered,false);
 await page.reload({waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>document.querySelectorAll('#community-cards article').length===1,null,{timeout:15000});
 report.reloadedTitle=await page.locator('#community-cards h3').textContent();assert.equal(report.reloadedTitle,'Static QA · Kung Fu');
 const afterReload=await page.evaluate(()=>JSON.parse(localStorage.getItem('q1-store-v1')));
 assert.equal(afterReload.purchases.length,1);assert.equal(Object.keys(afterReload.jobs).length,1);
 console.log(JSON.stringify(report,null,2));await fs.writeFile('/tmp/q1-static-qa-report.json',JSON.stringify(report,null,2));
}catch(error){report.error=error.stack;console.log(JSON.stringify(report,null,2));await fs.writeFile('/tmp/q1-static-qa-report.json',JSON.stringify(report,null,2));throw error;}
finally{await browser.close();}
