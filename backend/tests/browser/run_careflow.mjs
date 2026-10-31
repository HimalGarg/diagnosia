// Drives the CareFlow screen in Chrome. Run the demo first, then start the server yourself.
//   node backend/tests/browser/run_careflow.mjs <puppeteer-core path> <port> [--mongo-container NAME] [--expect-stream]
import { createRequire } from 'module';
import { execFileSync } from 'child_process';
const require = createRequire(import.meta.url);
const [tools, port, ...flags] = process.argv.slice(2);
const puppeteer = require(tools);
const BASE = `http://127.0.0.1:${port}`;
const container = flags.includes('--mongo-container') ? flags[flags.indexOf('--mongo-container') + 1] : null;
const expectStream = flags.includes('--expect-stream');
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const results = [];
const check = (name, passed, detail = '') => { results.push(passed); console.log(`${passed ? 'PASS' : 'FAIL'}  ${name}${detail ? `: ${detail}` : ''}`); };

const browser = await puppeteer.launch({ headless: 'new', executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args: ['--no-sandbox'] });
const page = await browser.newPage();
await page.setViewport({ width: 1280, height: 950 });
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
await page.goto(`${BASE}/login.html`, { waitUntil: 'domcontentloaded' });
const login = await page.evaluate(async () => (await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: 'careflow.demo.doctor@example.com', password: 'Synthetic-demo-only-1' }) })).status);
check('signs in', login === 200);
await page.goto(`${BASE}/care.html#/careflow`, { waitUntil: 'domcontentloaded' });
await wait(2500);
const text = () => page.evaluate(() => document.querySelector('#main').innerText);
const rows = () => page.evaluate(() => document.querySelectorAll('#main .timeline li').length);
check('opens CareFlow', (await text()).includes('CareFlow'));
check('shows the synthetic label', (await text()).includes('Synthetic demo data'));
const selects = await page.$$('.filters select');
const workflow = await page.evaluate((select) => [...select.options].map((option) => option.value).find((value) => value === 'case_demo_001') || select.options[1].value, selects[0]);
await selects[0].select(workflow);
await wait(1500);
const all = await rows();
check('shows events', all >= 20, `${all} events`);
const body = await text();
const source = body.match(/Timeline from[^\n]*/)?.[0] ?? '';
check('says where events came from', source.length > 0, source);
await selects[1].select('LabLens');
await wait(700);
const lab = await rows();
check('filters by tool', lab > 0 && lab < all, `${lab} of ${all}`);
await selects[1].select('');
await selects[2].select('review_required');
await wait(700);
const review = await rows();
check('filters by status', review > 0 && review < all, `${review} of ${all}`);
await selects[2].select('');
await wait(700);
const shown = await text();
check('shows handoffs', shown.includes('Handoffs between tools') && shown.includes('LabLens to ChronicCare'));
check('shows a review indicator', shown.includes('Needs review'));
check('counts what is still waiting', /\d+ still waiting for a person/.test(shown));
check('shows integration status', shown.includes('Integration status') && shown.includes('YugabyteDB'), (shown.match(/MongoDB Atlas\s+\w+/) ?? [''])[0]);
check('shows AI run figures', /AI runs from/.test(shown), (shown.match(/AI runs from[^\n]{0,70}/) ?? [''])[0]);

// A new event, with the page left alone.
const before = await rows();
const started = Date.now();
await page.evaluate(async () => {
    const list = await (await fetch('/api/patients')).json();
    const id = list.find((item) => item.first_name === 'Demo').id;
    await fetch('/api/addons/medguard/reconcile', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ patient_id: id }) });
});
let after = before;
while (after === before && Date.now() - started < 25000) { await wait(200); after = await rows(); }
const seconds = (Date.now() - started) / 1000;
check('updates without a reload', after > before, `${seconds.toFixed(1)}s`);
if (expectStream) check('live update beat the 15-second refresh', seconds < 12, `${seconds.toFixed(1)}s`);
else check('the 15-second refresh carries it without a stream', seconds <= 25, `${seconds.toFixed(1)}s`);

if (container) {
    execFileSync('docker', ['stop', '-t', '1', container]);
    await page.evaluate(() => { window.location.hash = '#/home'; });
    await wait(800);
    await page.evaluate(() => { window.location.hash = '#/careflow'; });
    await wait(4000);
    const pick = await page.$$('.filters select');
    await pick[0].select(workflow);
    await wait(5000);
    const down = await text();
    check('labels the fallback honestly while MongoDB is down', /local copy/.test(down), (down.match(/Timeline from[^\n]*/) ?? [''])[0]);
    check('still shows events while MongoDB is down', (await rows()) > 0);
    execFileSync('docker', ['start', container]);
}

await page.setRequestInterception(true);
const refuse = (request) => (request.url().includes('/timeline') ? request.respond({ status: 500, contentType: 'application/json', body: '{"detail":"Synthetic failure"}' }) : request.continue());
page.on('request', refuse);
await page.evaluate(() => { window.location.hash = '#/home'; });
await wait(600);
await page.evaluate(() => { window.location.hash = '#/careflow'; });
await wait(2000);
const pick = await page.$$('.filters select');
await pick[0].select(workflow);
await wait(1500);
check('shows a clear error state', (await text()).includes("The timeline couldn't load"));
page.off('request', refuse);
await page.setRequestInterception(false);
check('no page errors', errors.length === 0, errors.slice(0, 2).join(' | '));
await page.setViewport({ width: 390, height: 800 });
await wait(400);
check('fits a phone', (await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)) <= 0);
await browser.close();
console.log(`${results.filter(Boolean).length} of ${results.length} passed`);
process.exit(results.every(Boolean) ? 0 : 1);
