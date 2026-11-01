"""Synthetic browser check. Start `python -m http.server 8128 --directory .` and
headless Chrome with a disposable profile and `--remote-debugging-port=9229`.
Requires websockets, pdfinfo and pdftotext. Writes screenshots and PDFs to /private/tmp.
"""
import asyncio, base64, json, urllib.request, subprocess, time
import websockets

async def main():
    req = urllib.request.Request('http://127.0.0.1:9229/json/new?about:blank', method='PUT')
    target = json.load(urllib.request.urlopen(req))
    async with websockets.connect(target['webSocketDebuggerUrl'], max_size=20_000_000) as ws:
        seq = 0
        async def call(method, params=None):
            nonlocal seq
            seq += 1
            await ws.send(json.dumps({'id':seq,'method':method,'params':params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get('id') == seq:
                    if 'error' in msg: raise RuntimeError(msg['error'])
                    return msg.get('result', {})
        await call('Page.enable')
        for width in (1280, 768, 390):
            await call('Emulation.setDeviceMetricsOverride', {'width':width,'height':1000,'deviceScaleFactor':1,'mobile':width == 390})
            await call('Page.navigate', {'url':'http://127.0.0.1:8128/backend/tests/browser/fixtures/diagnofast-report.html?kind=typical'})
            await asyncio.sleep(2)
            result = await call('Runtime.evaluate', {'expression':"JSON.stringify({width:document.documentElement.clientWidth, scroll:document.documentElement.scrollWidth, text:document.querySelector('.requisition')?.innerText})", 'returnByValue':True})
            data=json.loads(result['result']['value'])
            assert data['width'] == width and data['scroll'] <= width, data
            assert 'Chest pain' in data['text'] and 'DEMO DATA' in data['text'] and 'Penicillin' in data['text']
            pic=await call('Page.captureScreenshot', {'format':'png'})
            open(f'/private/tmp/diagnofast-exact-{width}.png','wb').write(base64.b64decode(pic['data']))
            print('PASS viewport',width)
        for kind in ('short','typical','long','failed','old'):
            await call('Emulation.setDeviceMetricsOverride', {'width':1280,'height':1000,'deviceScaleFactor':1,'mobile':False})
            await call('Page.navigate', {'url':f'http://127.0.0.1:8128/backend/tests/browser/fixtures/diagnofast-report.html?kind={kind}'})
            await asyncio.sleep(2)
            pdf=await call('Page.printToPDF', {'preferCSSPageSize':True,'printBackground':True})
            path=f'/private/tmp/diagnofast-final-{kind}.pdf'
            open(path,'wb').write(base64.b64decode(pdf['data']))
            info=subprocess.check_output(['pdfinfo',path],text=True)
            text=subprocess.check_output(['pdftotext','-layout',path,'-'],text=True)
            assert 'Pages:           1' in info and '(A4)' in info, info
            assert 'PHYSICIAN WORKSPACE' in text and 'Chest pain' in text
            assert ('DEMO DATA' in text) == (kind != 'old')
            if kind == 'old': assert 'ABHA ID' not in text
            assert ('INCOMPLETE PRINT SUMMARY' in text) == (kind == 'long'), text
            if kind == 'failed': assert 'Report generation failed' in text
            else: assert 'Penicillin' in text and 'no fever' in text and 'onset uncertain' in text
            print('PASS PDF',kind)
asyncio.run(main())
