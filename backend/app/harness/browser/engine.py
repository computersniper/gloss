"""Per-session CDP pages, safe network admission, refs, tools and live viewers."""
from __future__ import annotations
import asyncio
import base64
import json
import time
import uuid
from itertools import count
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urljoin
from ... import config
from .. import settings, workspace
from .cdp import CDPError
from .discovery import discover
from .process import DESKTOP_BROWSER, BrowserProcess, desktop_endpoint
from .proxy import GuardProxy
from .security import validate_url, BrowserSecurityError
from .snapshot import SnapshotReferences
READ_JS = """(() => {
 const body=document.body; if(!body)return {text:'',byline:''};
 const candidates=[...document.querySelectorAll('article,main,[role=main]')];
 const source=candidates.sort((a,b)=>b.innerText.length-a.innerText.length)[0]||body;
 const clone=source.cloneNode(true);
 clone.querySelectorAll('nav,aside,footer,header,script,style,noscript,[role=navigation],[aria-hidden=true],.advertisement,.ads').forEach(n=>n.remove());
 return {text:clone.innerText||clone.textContent||'',byline:document.querySelector('meta[name=author]')?.content||''};
})()"""
INFO_JS = '({url:location.href,title:document.title})'
ELEMENT_INFO = "function(){return {tag:this.tagName,type:this.type||'',href:this.href||'',password:this.type==='password',submit:!!this.form&&((this.tagName==='BUTTON'&&this.type==='submit')||(this.tagName==='INPUT'&&['submit','image'].includes(this.type))),form:!!this.form};}"

class Page:
    _ref_epochs = count(1)

    def __init__(self, target, session, context):
        self.id, self.session, self.context = (target, session, context)
        self.url, self.title = ('about:blank', '')
        self.loading = False
        # Ref ranges remain distinct across tabs/sessions and crash restarts.
        self.refs = SnapshotReferences(counter_start=next(self._ref_epochs) * 1000000)
        self.tasks = []
        self.frames = set()
        self.screencasting = False

DEFAULT_VIEWPORT = (1280, 720)
MIN_VIEWPORT = (320, 240)
MAX_VIEWPORT = (1600, 1200)
SCREENCAST = {'format': 'jpeg', 'quality': 65, 'maxWidth': MAX_VIEWPORT[0], 'maxHeight': MAX_VIEWPORT[1], 'everyNthFrame': 2}


def _metrics(viewport):
    return {'width': viewport[0], 'height': viewport[1], 'deviceScaleFactor': 1, 'mobile': False}


class BrowserSession:

    def __init__(self, context):
        self.context = context
        self.pages = {}
        self.active = None
        self.controlling = 'agent'
        self.navigations = 0
        self.lock = asyncio.Lock()
        self.log = []
        # CSS viewport of every page, kept in step with the panel showing it so
        # pages lay themselves out for that size instead of being scaled down.
        self.viewport = DEFAULT_VIEWPORT

class BrowserManager:

    def __init__(self, sessions):
        self.sessions = sessions
        self.browser = None
        self.proxy = None
        self.states = {}
        self._lock = asyncio.Lock()
        self._client = None
        self._attachments = None
        self._close_task = None
        self._target_sessions = {}
        self._target_contexts = {}
        self._target_ready = asyncio.Condition()
        self._creating_contexts = set()
        self._context_lock = asyncio.Lock()
        self._applied_options = None

    def options(self):
        desired = settings.load_global().get('browser', {})
        if self._applied_options is not None:
            return {**desired, **{key: self._applied_options.get(key) for key in ('path', 'headed', 'allow_private')}}
        return desired

    async def status(self):
        opts = settings.load_global().get('browser', {})
        if desktop_endpoint():
            return {'available': True, 'browser': DESKTOP_BROWSER, 'path': None, 'headed': True, 'embedded': True, 'running': bool(self.browser and self.browser.client and not self.browser.client._closed), 'restart_pending': False}
        executable = await asyncio.to_thread(discover, opts.get('path'))
        return {'available': executable is not None, 'browser': executable.browser if executable else None, 'path': str(executable.path) if executable else opts.get('path'), 'headed': opts.get('headed', False), 'running': bool(self.browser and self.browser.process and (self.browser.process.returncode is None)), 'restart_pending': self._applied_options is not None and any((opts.get(key) != self._applied_options.get(key) for key in ('path', 'headed', 'allow_private')))}

    async def start(self):
        if self._close_task is not None and (not self._close_task.done()):
            await asyncio.shield(self._close_task)
        async with self._lock:
            opts = settings.load_global().get('browser', {})
            changed = self._applied_options is not None and any((opts.get(key) != self._applied_options.get(key) for key in ('path', 'headed', 'allow_private')))
            if self.browser and changed and (not self.browser._leases):
                if self._attachments:
                    self._attachments.cancel()
                    await asyncio.gather(self._attachments, return_exceptions=True)
                    self._attachments = None
                for sid in list(self.states):
                    await self.close_session(sid)
                await self.browser.close()
                await self.proxy.close()
                self.browser = None
                self._client = None
            if self.browser is None:
                self.proxy = GuardProxy(opts.get('allow_private', False))
                port = await self.proxy.start()
                self.browser = BrowserProcess(config.DATA_DIR, path=opts.get('path'), headed=opts.get('headed', False), idle_timeout=opts.get('idle_timeout', 300))
                self.browser.proxy_port = port
                self._applied_options = dict(opts)
            self.browser.idle_timeout = opts.get('idle_timeout', 300)
            client = await self.browser.start()
            if client is not self._client:
                if self._attachments:
                    self._attachments.cancel()
                    await asyncio.gather(self._attachments, return_exceptions=True)
                for state in self.states.values():
                    for page in state.pages.values():
                        for task in page.tasks:
                            task.cancel()
                self.states.clear()
                self._client = client
                self._target_sessions.clear()
                self._target_contexts.clear()
                # Close the launcher's initial page before pausing new renderers.
                # Closing an already debugger-paused startup tab can stall Edge.
                targets = await client.call('Target.getTargets')
                for target in targets['targetInfos']:
                    if target['type'] == 'page':
                        await client.call('Target.closeTarget', {'targetId': target['targetId']})
                ready = asyncio.Event()
                self._attachments = asyncio.create_task(self._guard_targets(client, ready))
                await ready.wait()
                await client.call('Target.setAutoAttach', {'autoAttach': True, 'waitForDebuggerOnStart': True, 'flatten': True, 'filter': [{'type': 'page', 'exclude': False}, {'exclude': True}]})
            return client

    async def _guard_targets(self, client, ready):
        async with client.events('Target.attachedToTarget', lossless=True) as queue:
            ready.set()
            while True:
                event = await queue.get()
                if event is None:
                    return
                info = event.get('targetInfo', {})
                # Explicit createTarget registers the owned page before resuming
                # its paused renderer. All unsolicited popups remain paused and
                # are closed, before any of their page code/network can execute.
                if info.get('type') == 'page':
                    owned = info.get('browserContextId') in self._creating_contexts
                    if owned:
                        async with self._target_ready:
                            self._target_sessions[info['targetId']] = event['sessionId']
                            self._target_contexts[info['targetId']] = info.get('browserContextId')
                            self._target_ready.notify_all()
                    else:
                        with suppress(CDPError, TimeoutError):
                            await client.call('Target.closeTarget', {'targetId': info['targetId']})
                else:
                    # Worker/OOPIF destinations still use the socket proxy.
                    with suppress(CDPError):
                        await client.call('Runtime.runIfWaitingForDebugger', session_id=event['sessionId'])

    async def call(self, page, method, params=None, **kwargs):
        if self._client is None:
            raise CDPError('Browser is closed')
        return await self._client.call(method, params, session_id=page.session, **kwargs)

    async def evaluate(self, page, expression):
        result = await self.call(page, 'Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True})
        if result.get('exceptionDetails'):
            raise CDPError('Page operation failed: ' + str(result['exceptionDetails'].get('text')))
        return result.get('result', {}).get('value')

    async def element(self, page, ref, function, arguments=None):
        node = page.refs.resolve(ref)
        obj = await self.call(page, 'DOM.resolveNode', {'backendNodeId': node.backend_node_id})
        object_id = obj['object']['objectId']
        try:
            result = await self.call(page, 'Runtime.callFunctionOn', {'objectId': object_id, 'functionDeclaration': function, 'arguments': [{'value': v} for v in arguments or []], 'returnByValue': True, 'awaitPromise': True})
            if result.get('exceptionDetails'):
                raise CDPError('Element detached or operation failed; snapshot again')
            return result.get('result', {}).get('value')
        finally:
            with suppress(CDPError):
                await self.call(page, 'Runtime.releaseObject', {'objectId': object_id})

    async def _new_page(self, sid, state):
        self._creating_contexts.add(state.context)
        target = None
        try:
            created = await self._client.call('Target.createTarget', {'url': 'about:blank', 'browserContextId': state.context})
            target = created['targetId']
            async with asyncio.timeout(15):
                async with self._target_ready:
                    await self._target_ready.wait_for(lambda: target in self._target_sessions)
            page = Page(target, self._target_sessions.pop(target), state.context)
        except BaseException:
            targets = {key for key, context in self._target_contexts.items() if context == state.context}
            if target:
                targets.add(target)
            for pending in targets:
                cleanup = asyncio.create_task(self._client.call('Target.closeTarget', {'targetId': pending}))
                with suppress(CDPError, TimeoutError):
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        await cleanup
                self._target_sessions.pop(pending, None)
                self._target_contexts.pop(pending, None)
            raise
        finally:
            self._creating_contexts.discard(state.context)
        # If a script produced a popup while this context was creating a page,
        # it also remains debugger-paused. Close every surplus attachment.
        for unsolicited in list(self._target_sessions):
            if self._target_contexts.get(unsolicited) == state.context and unsolicited not in {p.id for s in self.states.values() for p in s.pages.values()}:
                with suppress(CDPError, TimeoutError):
                    await self._client.call('Target.closeTarget', {'targetId': unsolicited})
                self._target_sessions.pop(unsolicited, None)
                self._target_contexts.pop(unsolicited, None)
        self._target_contexts.pop(target, None)
        state.pages[target] = page
        state.active = target
        try:
            await self._configure_page(sid, state, page)
        except BaseException:
            state.pages.pop(target, None)
            state.active = next(iter(state.pages), None)
            cleanup = asyncio.create_task(self._close_page(page))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
            raise
        return page

    async def _configure_page(self, sid, state, page):
        # Security consumers subscribe before enabling producers. Request queues
        # are lossless; screencast queues deliberately retain only latest frames.
        for event, handler in [('Fetch.requestPaused', self._request), ('Page.frameNavigated', self._navigated), ('Page.javascriptDialogOpening', self._dialog), ('Page.screencastFrame', self._frame)]:
            ready = asyncio.Event()

            async def listen(event=event, handler=handler, ready=ready):
                async with self._client.events(event, session_id=page.session, lossless=event == 'Fetch.requestPaused') as queue:
                    ready.set()
                    while True:
                        data = await queue.get()
                        if data is None:
                            return
                        await handler(sid, state, page, data)
            task = asyncio.create_task(listen(), name='glossx-browser-' + event)
            page.tasks.append(task)
            await ready.wait()
        await self.call(page, 'Emulation.setDeviceMetricsOverride', _metrics(state.viewport))
        for domain in ['Page', 'DOM', 'Accessibility', 'Runtime', 'Network']:
            await self.call(page, domain + '.enable')
        await self.call(page, 'Network.setBypassServiceWorker', {'bypass': True})
        await self.call(page, 'Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]})
        # Popups are prevented before scripts can open unguarded targets; users
        # and agents can explicitly create a tab through the guarded API.
        await self.call(page, 'Page.addScriptToEvaluateOnNewDocument', {'source': "Object.defineProperty(window,'open',{value:()=>null,writable:false,configurable:false});document.addEventListener('click',e=>{const a=e.target.closest?.('a[target]');if(a)a.target='_self';},true);"})
        await self._client.call('Browser.setDownloadBehavior', {'behavior': 'deny', 'browserContextId': state.context})
        await self.call(page, 'Runtime.runIfWaitingForDebugger')


    async def _dialog(self, sid, state, page, data):
        self.publish(sid, 'Dismissed ' + data.get('type', 'dialog') + ': ' + data.get('message', '')[:500])
        with suppress(CDPError):
            await self.call(page, 'Page.handleJavaScriptDialog', {'accept': False})

    async def _request(self, sid, state, page, data):
        try:
            await validate_url(data['request']['url'], allow_private=self.options().get('allow_private', False))
            if data.get('resourceType') == 'Document':
                budget = self.options().get('navigation_budget', 60)
                state.navigations += 1
                if state.navigations > budget:
                    raise BrowserSecurityError('Navigation budget exhausted for this session')
            await self.call(page, 'Fetch.continueRequest', {'requestId': data['requestId']})
        except (BrowserSecurityError, KeyError, OSError, TimeoutError):
            with suppress(CDPError):
                await self.call(page, 'Fetch.failRequest', {'requestId': data['requestId'], 'errorReason': 'BlockedByClient'})
        except CDPError:
            return

    async def _navigated(self, sid, state, page, data):
        if not data.get('frame', {}).get('parentId'):
            page.url = data['frame']['url']
            page.refs.invalidate()
            page.loading = False
            self.publish(sid)

    async def _frame(self, sid, state, page, data):
        with suppress(CDPError):
            await self.call(page, 'Page.screencastFrameAck', {'sessionId': data['sessionId']})
        frame = {'data': data['data'], 'width': data.get('metadata', {}).get('deviceWidth', state.viewport[0]), 'height': data.get('metadata', {}).get('deviceHeight', state.viewport[1]), 'session_id': sid}
        for queue in list(page.frames):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(frame)

    def view(self, sid):
        state = self.states.get(sid)
        tabs = [{'id': p.id, 'url': p.url, 'title': p.title, 'active': p.id == state.active} for p in state.pages.values()] if state else []
        page = state.pages.get(state.active) if state else None
        return {'tabs': tabs, 'active': state.active if state else None, 'url': page.url if page else 'about:blank', 'title': page.title if page else '', 'loading': page.loading if page else False, 'controlling': state.controlling if state else 'agent', 'log': state.log[-30:] if state else []}

    def publish(self, sid, note=None):
        if note and sid in self.states:
            self.states[sid].log.append({'time': time.time(), 'text': note})
        try:
            self.sessions._rt(sid).emit('browser.updated', self.view(sid))
        except LookupError:
            pass

    @asynccontextmanager
    async def operation(self, sid, *, user=False):
        self.sessions._rt(sid)
        await self.start()
        lease_owner = self.browser
        lease_owner.acquire()
        try:
            async with self._context_lock:
                state = self.states.get(sid)
                if state is None:
                    params = {'disposeOnDetach': True}
                    if desktop_endpoint():
                        # Desktop pages get the guard proxy per context, not by command line.
                        params['proxyServer'] = f'http://127.0.0.1:{self.browser.proxy_port}'
                    created = await self._client.call('Target.createBrowserContext', params)
                    state = BrowserSession(created['browserContextId'])
                    self.states[sid] = state
            async with state.lock:
                if not user and state.controlling == 'user':
                    raise CDPError('The user is controlling this browser; wait until control returns to the agent')
                if not state.pages:
                    await self._new_page(sid, state)
                yield (state, state.pages[state.active])
        finally:
            lease_owner.release()

    async def metadata(self, page):
        with suppress(CDPError):
            info = await self.evaluate(page, INFO_JS)
            page.url, page.title = (info['url'], info['title'])

    async def approval_reason(self, sid, name, args):
        if name in {'browser_download', 'browser_import_paper'}:
            return 'Download a file' if name == 'browser_download' else 'Import a paper into the library'
        if name == 'browser_type' and args.get('submit'):
            return 'Submit a form that may send data to this site'
        if name == 'browser_press' and str(args.get('key', '')).lower() in {'enter', 'return'}:
            return 'Press Enter, which may submit a form'
        if name in {'browser_type', 'browser_click'} and args.get('ref'):
            async with self.operation(sid) as (_, page):
                info = await self.element(page, args['ref'], ELEMENT_INFO)
                if info.get('password') and name == 'browser_type':
                    return 'Type into a password field'
                if info.get('submit') and name == 'browser_click':
                    return 'Submit a form that sends data to this site'
        return None

    async def set_viewport(self, sid, width, height):
        size = (max(MIN_VIEWPORT[0], min(int(width), MAX_VIEWPORT[0])), max(MIN_VIEWPORT[1], min(int(height), MAX_VIEWPORT[1])))
        async with self.operation(sid, user=True) as (state, _):
            if state.viewport != size:
                state.viewport = size
                for page in list(state.pages.values()):
                    with suppress(CDPError):
                        await self.call(page, 'Emulation.setDeviceMetricsOverride', _metrics(size))
        return {**self.view(sid), 'viewport': {'width': size[0], 'height': size[1]}}

    async def action(self, sid, name, args, *, user=False):
        args = args or {}
        async with self.operation(sid, user=user) as (state, page):
            before = await self.evaluate(page, "({url:location.href,title:document.title,text:document.body?.innerText?.slice(0,4000)||'',x:scrollX,y:scrollY})")
            if name == 'takeover':
                state.controlling = 'user' if args.get('controlling', True) in (True, 'user') else 'agent'
            elif name in {'open', 'navigate', 'new_tab'}:
                url = await validate_url(args.get('url') or 'about:blank', allow_private=self.options().get('allow_private', False))
                if name == 'new_tab' or args.get('new_tab'):
                    page = await self._new_page(sid, state)
                page.refs.invalidate()
                page.loading = True
                result = await self.call(page, 'Page.navigate', {'url': url}, timeout=45)
                if result.get('errorText'):
                    page.loading = False
                    raise CDPError('Navigation failed: ' + result['errorText'])
                # Wait boundedly for DOM readiness; interception runs separately.
                for _ in range(100):
                    if await self.evaluate(page, 'document.readyState') != 'loading':
                        break
                    await asyncio.sleep(0.05)
            elif name == 'snapshot':
                nodes = (await self.call(page, 'Accessibility.getFullAXTree'))['nodes']
                urls = {}
                for node in nodes:
                    if node.get('role', {}).get('value') == 'link' and node.get('backendDOMNodeId'):
                        obj = await self.call(page, 'DOM.resolveNode', {'backendNodeId': node['backendDOMNodeId']})
                        oid = obj['object'].get('objectId')
                        if oid:
                            link = await self.call(page, 'Runtime.callFunctionOn', {'objectId': oid, 'functionDeclaration': 'function(){return this.href||""}', 'returnByValue': True})
                            urls[node['backendDOMNodeId']] = link.get('result', {}).get('value', '')
                            await self.call(page, 'Runtime.releaseObject', {'objectId': oid})
                result = page.refs.render(nodes, urls=urls, ref=args.get('ref'), find=args.get('find'))
                await self.metadata(page)
                return {**self.view(sid), **result}
            elif name in {'read', 'find'}:
                content = await self.evaluate(page, READ_JS)
                text = content.get('text', '')
                if name == 'find':
                    needle = str(args.get('text', ''))
                    positions = []
                    start = 0
                    while len(positions) < 30:
                        hit = text.casefold().find(needle.casefold(), start)
                        if hit < 0 or not needle:
                            break
                        positions.append({'offset': hit, 'text': text[max(0, hit - 120):hit + len(needle) + 200]})
                        start = hit + len(needle)
                    return {**self.view(sid), 'matches': positions}
                n = max(1, min(int(args.get('page') or 1), 1000))
                offset = (n - 1) * 12000
                await self.metadata(page)
                return {**self.view(sid), 'text': text[offset:offset + 12000], 'byline': content.get('byline'), 'page': n, 'has_more': len(text) > offset + 12000}
            elif name == 'click':
                if args.get('ref'):
                    info = await self.element(page, args['ref'], ELEMENT_INFO)
                    if info.get('href'):
                        await validate_url(info['href'], allow_private=self.options().get('allow_private', False))
                    await self.element(page, args['ref'], 'function(){this.scrollIntoView({block:"center"});this.click();}')
                else:
                    for kind in ['mousePressed', 'mouseReleased']:
                        await self.call(page, 'Input.dispatchMouseEvent', {'type': kind, 'x': float(args.get('x', 0)), 'y': float(args.get('y', 0)), 'button': 'left', 'clickCount': 1})
            elif name == 'type':
                await self.element(page, args['ref'], 'function(){this.focus();this.value="";this.dispatchEvent(new Event("input",{bubbles:true}));}')
                await self.call(page, 'Input.insertText', {'text': str(args.get('text', ''))[:100000]})
                if args.get('submit'):
                    await self.key(page, 'Enter')
            elif name == 'text':
                await self.call(page, 'Input.insertText', {'text': str(args.get('text', ''))[:100000]})
            elif name == 'select':
                await self.element(page, args['ref'], 'function(v){this.value=v;this.dispatchEvent(new Event("input",{bubbles:true}));this.dispatchEvent(new Event("change",{bubbles:true}));}', [args['value']])
            elif name in {'press', 'key'}:
                await self.key(page, args.get('key', ''))
            elif name == 'scroll':
                if args.get('ref'):
                    await self.element(page, args['ref'], 'function(){this.scrollIntoView({block:"center"});}')
                else:
                    dy = args.get('delta_y', -600 if args.get('direction') == 'up' else 600)
                    await self.call(page, 'Input.dispatchMouseEvent', {'type': 'mouseWheel', 'x': float(args.get('x', 400)), 'y': float(args.get('y', 300)), 'deltaX': float(args.get('delta_x', 0)), 'deltaY': float(dy)})
            elif name in {'back', 'forward'}:
                history = await self.call(page, 'Page.getNavigationHistory')
                i = history['currentIndex'] + (-1 if name == 'back' else 1)
                if 0 <= i < len(history['entries']):
                    await validate_url(history['entries'][i]['url'], allow_private=self.options().get('allow_private', False))
                    page.refs.invalidate()
                    await self.call(page, 'Page.navigateToHistoryEntry', {'entryId': history['entries'][i]['id']})
            elif name == 'reload':
                page.refs.invalidate()
                await self.call(page, 'Page.reload')
            elif name == 'switch_tab':
                if args.get('id') not in state.pages:
                    raise LookupError('Tab not found in this session')
                state.active = args['id']
                page = state.pages[state.active]
                await self.call(page, 'Page.bringToFront')
            elif name == 'close_tab':
                target = args.get('id') or state.active
                if target not in state.pages:
                    raise LookupError('Tab not found in this session')
                await self._close_page(state.pages.pop(target))
                if not state.pages:
                    page = await self._new_page(sid, state)
                else:
                    state.active = next(iter(state.pages))
                    page = state.pages[state.active]
            elif name == 'screenshot':
                params = {'format': 'png'}
                if args.get('ref'):
                    r = page.refs.resolve(args['ref'])
                    box = await self.call(page, 'DOM.getBoxModel', {'backendNodeId': r.backend_node_id})
                    q = box['model']['border']
                    params['clip'] = {'x': min(q[0::2]), 'y': min(q[1::2]), 'width': max(q[0::2]) - min(q[0::2]), 'height': max(q[1::2]) - min(q[1::2]), 'scale': 1}
                data = await self.call(page, 'Page.captureScreenshot', params)
                rel = 'downloads/screenshot-' + uuid.uuid4().hex[:10] + '.png'
                root = workspace.root_for(self.sessions._rt(sid).session.get('project_id'))
                path = workspace.resolve(root, rel)
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(base64.b64decode(data['data']))
                return {**self.view(sid), 'path': rel, 'file_ref': rel, 'image_url': f'/api/sessions/{sid}/browser/files/{path.name}', 'note': 'Screenshot saved'}
            elif name == 'download':
                url = args.get('url')
                if not url:
                    url = (await self.element(page, args['ref'], ELEMENT_INFO)).get('href')
                return await self.download(sid, url)
            elif name == 'import_paper':
                url = args.get('url') or page.url
                await validate_url(url, allow_private=self.options().get('allow_private', False))
                # Existing importer, but fetch all network data through the pinned
                # proxy rather than its unrestricted URL downloader.
                return await self.import_paper(sid, url, page)
            elif name != 'tabs':
                raise ValueError('Unknown browser action: ' + name)
            await asyncio.sleep(0.08)
            await self.metadata(page)
            after = await self.evaluate(page, "({url:location.href,title:document.title,text:document.body?.innerText?.slice(0,4000)||'',x:scrollX,y:scrollY})")
            changes = []
            if before['url'] != after['url']:
                changes.append('URL: ' + before['url'] + ' → ' + after['url'])
            if before['title'] != after['title']:
                changes.append('Title: ' + after['title'])
            if before['text'] != after['text']:
                changes.append('Visible page text changed; read or snapshot for details')
            if (before['x'], before['y']) != (after['x'], after['y']):
                changes.append(f"Scroll: ({after['x']}, {after['y']})")
            note = name.replace('_', ' ') + ' completed' + ('; ' + '; '.join(changes) if changes else '; page URL and visible text unchanged')
            self.publish(sid, note)
            return {**self.view(sid), 'note': note, 'changes': changes}

    async def key(self, page, key):
        keys = {'Enter': 13, 'Tab': 9, 'Escape': 27, 'Backspace': 8, 'ArrowDown': 40, 'ArrowUp': 38, 'ArrowLeft': 37, 'ArrowRight': 39, 'Delete': 46, 'Space': 32}
        code = keys.get(key, ord(key.upper()) if len(key) == 1 else 0)
        for kind in ['keyDown', 'keyUp']:
            await self.call(page, 'Input.dispatchKeyEvent', {'type': kind, 'key': key, 'windowsVirtualKeyCode': code, 'nativeVirtualKeyCode': code})

    async def fetch(self, url):
        import httpx
        await validate_url(url, allow_private=self.options().get('allow_private', False))
        await self.start()
        port = self.proxy.server.sockets[0].getsockname()[1]
        # Follow redirects explicitly so every URL is checked. The proxy also
        # validates and pins DNS at the actual socket for each hop.
        async with httpx.AsyncClient(proxy=f'http://127.0.0.1:{port}', trust_env=False, timeout=40) as client:
            for _ in range(8):
                async with client.stream('GET', url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers['location'])
                        await validate_url(url, allow_private=self.options().get('allow_private', False))
                        continue
                    response.raise_for_status()
                    parts = []
                    size = 0
                    async for part in response.aiter_bytes():
                        size += len(part)
                        if size > 50000000:
                            raise ValueError('Download exceeds 50 MB limit')
                        parts.append(part)
                    return (b''.join(parts), str(response.url), response.headers.get('content-type', ''))
        raise ValueError('Too many download redirects')

    async def download(self, sid, url):
        data, url, mime = await self.fetch(url)
        root = workspace.root_for(self.sessions._rt(sid).session.get('project_id'))
        from urllib.parse import urlsplit
        leaf = Path(urlsplit(url).path).name
        leaf = ''.join((c for c in leaf if c.isalnum() or c in '.-_'))[:100] or 'download.bin'
        rel = 'downloads/' + uuid.uuid4().hex[:8] + '-' + leaf
        path = workspace.resolve(root, rel)
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        return {**self.view(sid), 'path': rel, 'size': len(data), 'mime': mime, 'url': url, 'note': 'Download saved in workspace downloads/'}

    async def import_paper(self, sid, url, page):
        from ...library import service
        from ...agent import projects
        from urllib.parse import urlsplit
        from ...library import importers
        import httpx
        manager = self

        class GuardedImporterClient:

            async def get(self, destination):
                data, final, mime = await manager.fetch(destination)
                return httpx.Response(200, content=data, headers={'content-type': mime}, request=httpx.Request('GET', final))

        @asynccontextmanager
        async def guarded_client(**kwargs):
            yield GuardedImporterClient()
        parsed = urlsplit(url)
        pdf = url
        meta = {'source': 'browser', 'url': url, 'title': page.title or ''}
        if parsed.hostname in {'doi.org', 'dx.doi.org'}:
            doi = parsed.path.lstrip('/')
            meta, data = await importers.fetch_doi(doi, client_factory=guarded_client)
            if data is None:
                candidate = await self.evaluate(page, 'document.querySelector(\'meta[name="citation_pdf_url"]\')?.content||\'\'')
                if not candidate:
                    raise ValueError('No open-access PDF found for this DOI; open its PDF or upload it')
                data, final, mime = await self.fetch(urljoin(page.url, candidate))
            meta['url'] = url
            paper = await asyncio.to_thread(service.create_from_pdf_bytes, data, meta)
            await asyncio.to_thread(projects.ensure_paper_project_quietly, paper)
            self.publish(sid, 'DOI paper imported into library: ' + str(paper.get('title', doi)))
            return {**self.view(sid), 'paper': paper, 'paper_id': paper['id'], 'note': 'DOI paper imported into library'}
        if parsed.hostname and parsed.hostname.endswith('arxiv.org') and ('/abs/' in parsed.path):
            pdf = url.replace('/abs/', '/pdf/').split('?')[0]
        elif '/pdf/' not in parsed.path and (not parsed.path.lower().endswith('.pdf')):
            candidate = await self.evaluate(page, 'document.querySelector(\'meta[name="citation_pdf_url"]\')?.content||[...document.querySelectorAll(\'a[href]\')].find(a=>a.href.match(/\\.pdf($|[?#])/i))?.href||\'\'')
            if candidate:
                pdf = urljoin(url, candidate)
        data, final, mime = await self.fetch(pdf)
        if not data.startswith(b'%PDF'):
            raise ValueError('No PDF found on this page; open its PDF or pass a PDF URL')
        paper = await asyncio.to_thread(service.create_from_pdf_bytes, data, meta)
        await asyncio.to_thread(projects.ensure_paper_project_quietly, paper)
        self.publish(sid, 'Paper imported')
        return {**self.view(sid), 'paper': paper, 'paper_id': paper['id'], 'note': 'Paper imported into library'}

    async def _close_page(self, page):
        for task in page.tasks:
            task.cancel()
        await asyncio.gather(*page.tasks, return_exceptions=True)
        for queue in list(page.frames):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(None)
        with suppress(CDPError, TimeoutError):
            await self._client.call('Target.closeTarget', {'targetId': page.id})

    async def close_session(self, sid):
        state = self.states.pop(sid, None)
        if state:
            for page in list(state.pages.values()):
                await self._close_page(page)
            with suppress(CDPError):
                await self._client.call('Target.disposeBrowserContext', {'browserContextId': state.context})

    async def frames(self, sid):
        await self.start()
        lease_owner = self.browser
        lease_owner.acquire()
        page = None
        queue = asyncio.Queue(maxsize=1)
        try:
            async with self.operation(sid, user=True) as (state, page):
                page.frames.add(queue)
                if not page.screencasting:
                    await self.call(page, 'Page.startScreencast', SCREENCAST)
                    page.screencasting = True
                # Guarantee an initial frame for a static page.
                shot = await self.call(page, 'Page.captureScreenshot', {'format': 'jpeg', 'quality': 65})
                initial = {'data': shot['data'], 'width': state.viewport[0], 'height': state.viewport[1], 'session_id': sid}
            yield initial
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), 1)
                except asyncio.TimeoutError:
                    state = self.states.get(sid)
                    if not state or self._client is None or self._client._closed:
                        return
                    if state and state.active != page.id:
                        page.frames.discard(queue)
                        if not page.frames and page.screencasting:
                            with suppress(CDPError):
                                await self.call(page, 'Page.stopScreencast')
                            page.screencasting = False
                        page = state.pages[state.active]
                        page.frames.add(queue)
                        if not page.screencasting:
                            await self.call(page, 'Page.startScreencast', SCREENCAST)
                            page.screencasting = True
                        shot = await self.call(page, 'Page.captureScreenshot', {'format': 'jpeg', 'quality': 65})
                        yield {'data': shot['data'], 'width': state.viewport[0], 'height': state.viewport[1], 'session_id': sid}
                    else:
                        yield None
                    continue
                if frame is None:
                    state = self.states.get(sid)
                    if state and state.active != page.id:
                        continue
                    return
                yield frame
                await asyncio.sleep(0.1)
        finally:
            if page:
                page.frames.discard(queue)
                if not page.frames and page.screencasting:
                    with suppress(CDPError):
                        await self.call(page, 'Page.stopScreencast')
                    page.screencasting = False
            lease_owner.release()

    async def close(self):
        if self._close_task is None or self._close_task.done():
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        if self._attachments:
            self._attachments.cancel()
            await asyncio.gather(self._attachments, return_exceptions=True)
            self._attachments = None
        for sid in list(self.states):
            await self.close_session(sid)
        if self.browser:
            await self.browser.close()
        if self.proxy:
            await self.proxy.close()
        self.browser = None
        self.proxy = None
        self._client = None
        self._applied_options = None
