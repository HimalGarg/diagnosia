// Real Chrome, real MediaRecorder, fake audio device. Speech is faked by serve.py.
// See docs/EXTERNAL_INTEGRATION_VALIDATION.md for how to run this.
import { createRequire } from 'module';
import { execFileSync } from 'child_process';

const [modulesPath, port, dbPath, python] = process.argv.slice(2);
const require = createRequire(`${modulesPath}/`);
const puppeteer = require('puppeteer-core');
const BASE = `http://127.0.0.1:${port}`;
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const results = [];
const check = (name, passed, detail = '') => {
    results.push({ name, passed });
    console.log(`${passed ? 'PASS' : 'FAIL'}  ${name}${detail ? `: ${detail}` : ''}`);
};
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function launch(args = []) {
    return puppeteer.launch({ headless: 'new', executablePath: CHROME, args: ['--no-sandbox', ...args] });
}
const FAKE_MIC = ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'];

async function signedIn(browser, email) {
    const page = await browser.newPage();
    page.errors = [];
    page.on('pageerror', (error) => page.errors.push(error.message));
    await page.goto(`${BASE}/login.html`);
    await page.evaluate(async (address) => {
        const body = { first_name: 'Synthetic', last_name: 'Doctor', email: address, password: 'a-long-password', role: 'doctor' };
        await fetch('/api/auth/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    }, email);
    execFileSync(python, ['-c', `
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("UPDATE users SET account_status = 'verified' WHERE email = ?", (sys.argv[2],))
db.commit()`, dbPath, email]);
    return page;
}

const call = (page, path, body, method) => page.evaluate(async (url, payload, verb) => {
    const response = await fetch(url, { method: verb || (payload ? 'POST' : 'GET'), headers: payload ? { 'Content-Type': 'application/json' } : {}, body: payload ? JSON.stringify(payload) : undefined });
    return { status: response.status, body: await response.json().catch(() => null) };
}, path, body, method);

async function openSession(page, consent = true) {
    const patient = (await call(page, '/api/patients', { first_name: 'Synthetic', last_name: 'Patient' })).body;
    if (consent) await call(page, `/api/patients/${patient.id}/consents`, { purpose: 'ai_scribe' });
    await call(page, `/api/patients/${patient.id}/encounters`, { reason: 'Synthetic visit' });
    await page.goto(`${BASE}/care.html#/clinscribe`);
    await page.evaluate((id) => sessionStorage.setItem('care-patient', id), patient.id);
    await page.reload();
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some((button) => button.textContent === 'Open a session'), { timeout: 8000 });
    await clickText(page, 'Open a session');
    await wait(500);
    return patient;
}
const watchStreams = (page) => page.evaluate(() => {
    window.__streams = [];
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async (constraints) => {
        const stream = await original(constraints);
        window.__streams.push(stream);
        return stream;
    };
});
const clickText = (page, text) => page.evaluate((label) => {
    const button = [...document.querySelectorAll('button')].find((item) => item.textContent.trim() === label);
    if (!button) return false;
    button.click();
    return true;
}, text);
const speechState = (page) => page.evaluate(async () => (await fetch('/test/speech-state')).json());
const setSpeech = (page, value) => page.evaluate(async (mode) => { await fetch(`/test/speech-mode?value=${mode}`, { method: 'POST' }); }, value);
const warning = (page) => page.evaluate(() => { const item = document.getElementById('mic-warning'); return item && !item.hidden ? item.textContent : ''; });
const recordingShown = (page) => page.evaluate(() => document.querySelector('.recording')?.classList.contains('on'));
const lines = (page) => page.evaluate(() => document.querySelectorAll('.transcript div').length);

// Granted, normal recording, pause, last chunk kept.
{
    const browser = await launch(FAKE_MIC);
    const page = await signedIn(browser, 'granted@example.com');
    await openSession(page);
    await watchStreams(page);
    check('nothing records before a click', !(await recordingShown(page)));
    const before = (await speechState(page)).calls;
    await clickText(page, 'Start recording');
    await wait(1500);
    check('recording state is shown', await recordingShown(page));
    await wait(1500);
    await clickText(page, 'Pause');
    await wait(1200);
    const after = await speechState(page);
    check('the final chunk is uploaded on pause, not lost', after.calls === before + 1, `${after.calls - before} chunk`);
    check('audio reached transcription', after.bytes > 2000, `${after.bytes} bytes`);
    check('recording state clears', !(await recordingShown(page)));
    check('the line appears in the transcript', (await lines(page)) === 1);

    // Repeated recording in the same session.
    await clickText(page, 'Start recording');
    await wait(2500);
    await clickText(page, 'Stop and finish');
    await wait(1200);
    check('a second recording works', (await speechState(page)).calls === before + 2);
    check('stopping ends the session', await page.evaluate(() => document.querySelector('.recording span:last-child').textContent === 'Session ended'));
    check('the microphone is released when stopped', await page.evaluate(() => window.__streams.length > 0 && window.__streams.every((stream) => stream.getTracks().every((track) => track.readyState === 'ended'))));
    check('no page errors', page.errors.length === 0, page.errors.join('; '));
    await browser.close();
}

// Empty recording: stopped too soon to hold audio.
{
    const browser = await launch(FAKE_MIC);
    const page = await signedIn(browser, 'empty@example.com');
    await openSession(page);
    const before = (await speechState(page)).calls;
    await clickText(page, 'Start recording');
    await wait(60);
    await clickText(page, 'Pause');
    await wait(800);
    const sent = (await speechState(page)).calls - before;
    check('an empty recording uploads nothing or nothing harmful', sent <= 1 && !(await recordingShown(page)), `${sent} chunks`);
    await browser.close();
}

// Transcription failure: the session survives and the loss is shown.
{
    const browser = await launch(FAKE_MIC);
    const page = await signedIn(browser, 'failure@example.com');
    await openSession(page);
    await setSpeech(page, 'fail');
    await clickText(page, 'Start recording');
    await wait(2500);
    await clickText(page, 'Pause');
    await wait(1200);
    check('a transcription failure is shown', (await warning(page)).includes('could not be transcribed'), await warning(page));
    check('no invented line appears', (await lines(page)) === 0 || (await page.evaluate(() => document.querySelector('.transcript').textContent.includes('Lines appear here'))));
    await setSpeech(page, 'empty');
    await clickText(page, 'Start recording');
    await wait(2500);
    await clickText(page, 'Stop and finish');
    await wait(1000);
    await setSpeech(page, 'ok');
    check('an empty transcript adds no line', await page.evaluate(() => document.querySelector('.transcript').textContent.includes('Lines appear here')));
    await browser.close();
}

// Network failure mid-recording.
{
    const browser = await launch(FAKE_MIC);
    const page = await signedIn(browser, 'network@example.com');
    await openSession(page);
    await clickText(page, 'Start recording');
    await wait(1500);
    await page.setOfflineMode(true);
    await clickText(page, 'Pause');
    await wait(1200);
    check('a network failure is shown, not hidden', (await warning(page)).length > 0, await warning(page));
    await page.setOfflineMode(false);
    await browser.close();
}

// Permission denied.
{
    const browser = await launch();
    const context = browser.defaultBrowserContext();
    await context.overridePermissions(BASE, []);
    const page = await signedIn(browser, 'denied@example.com');
    await openSession(page);
    await page.evaluate(() => {
        navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('denied', 'NotAllowedError'));
    });
    const uploadsBefore = (await speechState(page)).calls;
    await clickText(page, 'Start recording');
    await wait(800);
    check('permission refusal is explained', (await warning(page)).includes('permission'), await warning(page));
    check('state returns to not recording', !(await recordingShown(page)));
    check('nothing was uploaded on refusal', (await speechState(page)).calls === uploadsBefore);
    check('the session is left paused on refusal', await page.evaluate(async () => {
        const row = document.querySelector('.recording span:last-child');
        return row.textContent === 'Not recording';
    }));
    await browser.close();
}

// No microphone, and microphone busy.
for (const [name, label, text] of [['NotFoundError', 'no microphone', 'No microphone'], ['NotReadableError', 'microphone in use', 'in use']]) {
    const browser = await launch();
    const page = await signedIn(browser, `${name.toLowerCase()}@example.com`);
    await openSession(page);
    await page.evaluate((error) => {
        navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('x', error));
    }, name);
    await clickText(page, 'Start recording');
    await wait(800);
    check(`${label} is explained`, (await warning(page)).includes(text), await warning(page));
    await browser.close();
}

// Unsupported browser.
{
    const browser = await launch();
    const page = await signedIn(browser, 'unsupported@example.com');
    await openSession(page);
    await page.evaluate(() => { delete window.MediaRecorder; window.MediaRecorder = undefined; });
    await clickText(page, 'Start recording');
    await wait(500);
    check('an unsupported browser is explained', (await warning(page)).includes('cannot record'), await warning(page));
    check('typing still works', await page.evaluate(() => !!document.querySelector('textarea')));
    await browser.close();
}

// No consent: no microphone, no upload.
{
    const browser = await launch(FAKE_MIC);
    const page = await signedIn(browser, 'consent@example.com');
    const patient = await openSession(page);
    const consents = (await call(page, `/api/patients/${patient.id}/consents`)).body.consents;
    await call(page, `/api/patients/${patient.id}/consents/${consents[0].id}/revoke`, {});
    await page.evaluate(() => {
        window.__asked = 0;
        const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getUserMedia = (constraints) => { window.__asked += 1; return original(constraints); };
    });
    const before = (await speechState(page)).calls;
    await clickText(page, 'Start recording');
    await wait(1500);
    check('revoked consent never opens the microphone', (await page.evaluate(() => window.__asked)) === 0);
    check('revoked consent uploads nothing', (await speechState(page)).calls === before);
    await browser.close();
}

// Leaving the page while recording.
{
    const browser = await launch(FAKE_MIC);
    const page = await signedIn(browser, 'leave@example.com');
    const patient = await openSession(page);
    await watchStreams(page);
    await clickText(page, 'Start recording');
    await wait(1500);
    await page.evaluate(() => { window.location.hash = '#/overview'; });
    await wait(1500);
    check('leaving the tool releases the microphone', await page.evaluate(() => window.__streams.every((stream) => stream.getTracks().every((track) => track.readyState === 'ended'))));
    const sessions = (await call(page, `/api/addons/clinscribe/patients/${patient.id}/sessions`)).body;
    check('leaving the tool pauses the session on the server', sessions[0].state === 'paused', sessions[0].state);
    await browser.close();
}

// Stop pressed while the browser is still opening the microphone.
{
    const browser = await launch(FAKE_MIC);
    const page = await signedIn(browser, 'race@example.com');
    await openSession(page);
    await page.evaluate(() => {
        window.__streams = [];
        const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getUserMedia = async (constraints) => {
            await new Promise((resolve) => setTimeout(resolve, 1500));
            const stream = await original(constraints);
            window.__streams.push(stream);
            return stream;
        };
    });
    const before = (await speechState(page)).calls;
    await clickText(page, 'Start recording');
    await wait(300);
    await clickText(page, 'Stop and finish');
    await wait(2500);
    check('stopping during the permission step never leaves the microphone on', await page.evaluate(() => window.__streams.length === 1 && window.__streams[0].getTracks().every((track) => track.readyState === 'ended')));
    check('nothing uploads after stopping early', (await speechState(page)).calls === before);
    await browser.close();
}

// Long recording, past one chunk boundary.
{
    const browser = await launch(FAKE_MIC);
    const page = await signedIn(browser, 'long@example.com');
    await openSession(page);
    const before = (await speechState(page)).calls;
    await clickText(page, 'Start recording');
    await wait(27000);
    await clickText(page, 'Stop and finish');
    await wait(1500);
    const sent = (await speechState(page)).calls - before;
    check('a long recording splits into chunks', sent === 2, `${sent} chunks over 27 seconds`);
    await browser.close();
}

const failed = results.filter((item) => !item.passed);
console.log(`\n${results.length - failed.length} of ${results.length} passed`);
process.exit(failed.length ? 1 : 0);
