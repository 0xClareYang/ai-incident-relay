"""AI Incident Relay: stdlib-only evidence storage, validation and rendering."""
import argparse
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import sys
import time
import uuid
import queue
import threading
import shutil
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from schema import check

ROOT = Path(__file__).resolve().parent
STATES = {'supported', 'contradicted', 'disputed', 'insufficient_evidence', 'inference'}
ID = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
MAX_BYTES = 2_000_000
TIMEOUT = 15


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key]=value
        return result
    def invalid_constant(value):
        raise ValueError('non-finite JSON number')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'),object_pairs_hook=unique,
                      parse_constant=invalid_constant)


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def contained(base, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('snapshot path must be relative')
    p = (Path(base) / relative).resolve()
    if not p.is_relative_to(Path(base).resolve()):
        raise ValueError('snapshot path leaves bundle')
    return p


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'template'):
            self.skip += 1
        if not self.skip and tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'section', 'tr'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'template'):
            self.skip = max(0, self.skip - 1)
        if not self.skip and tag in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'section', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        return '\n'.join(x for line in ''.join(self.parts).splitlines()
                         if (x := ' '.join(line.split()))) + '\n'


def url_target(url):
    if any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise ValueError('control characters blocked')
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('only public HTTP(S) URLs without credentials are supported')
    if p.query or p.fragment:
        raise ValueError('v1 accepts public document URLs without queries or fragments')
    port = p.port or (443 if p.scheme == 'https' else 80)
    if port != (443 if p.scheme == 'https' else 80):
        raise ValueError('nonstandard port blocked')
    host = p.hostname.encode('idna').decode('ascii')
    result = queue.Queue()
    def resolve():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as exc:
            result.put(exc)
    threading.Thread(target=resolve, daemon=True).start()
    try:
        addresses = result.get(timeout=5)
    except queue.Empty:
        raise ValueError('DNS timeout') from None
    if isinstance(addresses,Exception):
        raise addresses
    if not addresses:
        raise ValueError('no address')
    for entry in addresses:
        ip = ipaddress.ip_address(entry[4][0])
        if not ip.is_global or (ip.version == 6 and ip.ipv4_mapped and not ip.ipv4_mapped.is_global):
            raise ValueError('non-public address blocked')
        if ip.version==6 and (ip.sixtofour is not None or ip.teredo is not None
                              or ip in ipaddress.ip_network('64:ff9b::/96')
                              or ip in ipaddress.ip_network('64:ff9b:1::/48')):
            raise ValueError('IPv6 translation/tunnel address blocked')
    return p, host, port, addresses[0][4][0]


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, address):
        super().__init__(host, port, timeout=TIMEOUT, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Connect to the validated address, retaining hostname TLS verification/SNI.
        sock = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def fetch(url):
    started = time.monotonic()
    def remaining():
        budget=45-(time.monotonic()-started)
        if budget<=0:
            raise ValueError('total fetch deadline exceeded')
        return min(TIMEOUT,budget)
    redirects = []
    for _ in range(4):
        if time.monotonic() - started > 45:
            raise ValueError('total fetch deadline exceeded')
        p, host, port, address = url_target(url)
        if p.scheme == 'https':
            conn = PinnedHTTPS(host, port, address)
        else:
            conn = http.client.HTTPConnection(address, port, timeout=TIMEOUT)
        conn.timeout=remaining()
        try:
            conn.request('GET', p.path or '/', headers={
                'Host': host, 'User-Agent': 'AI-Incident-Relay/0.1', 'Accept-Encoding': 'identity'})
            if conn.sock is not None:
                conn.sock.settimeout(remaining())
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                target = response.getheader('Location')
                if not target:
                    raise ValueError('redirect without location')
                next_url = urljoin(url, target)
                if p.scheme == 'https' and urlsplit(next_url).scheme != 'https':
                    raise ValueError('HTTPS downgrade blocked')
                redirects.append(url)
                url = next_url
                continue
            if response.status != 200:
                raise ValueError('HTTP status ' + str(response.status))
            ctype = response.getheader('Content-Type', '').split(';')[0].lower()
            if ctype not in ('text/html', 'text/plain'):
                raise ValueError('unsupported content type')
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise ValueError('compressed response not supported')
            length = response.getheader('Content-Length')
            if length and int(length) > MAX_BYTES:
                raise ValueError('response too large')
            chunks = []
            size = 0
            while True:
                if time.monotonic() - started > 45:
                    raise ValueError('total fetch deadline exceeded')
                if conn.sock is not None:
                    conn.sock.settimeout(remaining())
                block = response.read1(min(65536, MAX_BYTES + 1 - size))
                if not block:
                    break
                chunks.append(block)
                size += len(block)
                if size > MAX_BYTES:
                    raise ValueError('response too large')
            raw = b''.join(chunks)
            charset = response.headers.get_content_charset() or 'utf-8'
            decoded = raw.decode(charset, errors='strict')
            if ctype == 'text/html':
                parser = TextExtractor()
                parser.feed(decoded)
                text = parser.text()
            else:
                text = decoded.replace('\r\n', '\n')
            if not text.strip():
                raise ValueError('empty source text')
            return raw, text, url, redirects
        finally:
            conn.close()
    raise ValueError('redirect limit exceeded')


def capture(url, out, source_id, title, publisher, source_type='primary_disclosure'):
    if not ID.fullmatch(source_id):
        raise ValueError('invalid source ID')
    p = urlsplit(url)
    if p.username or p.password or p.query or p.fragment:
        raise ValueError('URL credentials/parameters cannot be logged')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if (out / (source_id + '.json')).exists():
        raise ValueError('source ID already captured; use a new version ID')
    started = time.monotonic()
    source = dict(source_id=source_id, title=title, url=url, publisher=publisher,
                  published_at=None, updated_at=None, event_time=None, retrieved_at=now(),
                  time_notes='Publication/update/event times require source review.',
                  source_type=source_type, source_chain_id=source_id, derived_from=[],
                  relationship='pending review', interests='pending review',
                  access_status='failed', read_scope='none', snapshot=None,
                  acquisition={'tool':'stdlib HTTP client','provenance':'direct response bytes'})
    try:
        raw, text, final_url, redirects = fetch(url)
        raw_name, text_name = source_id + '.raw', source_id + '.txt'
        (out / raw_name).write_bytes(raw)
        data = text.encode('utf-8')
        (out / text_name).write_bytes(data)
        source.update(access_status='read', read_scope='captured document text; semantic review pending',
                      final_url=final_url, redirects=redirects,
                      snapshot=dict(path=text_name, sha256=digest(data), raw_path=raw_name,
                                    raw_sha256=digest(raw), extractor='stdlib-html-v1'))
    except (ValueError, OSError, http.client.HTTPException, UnicodeError, LookupError) as exc:
        # Do not log exception text, which could contain remote payloads or URL secrets.
        source['failure'] = dict(code=type(exc).__name__, message='Capture failed; no substitute evidence used.')
    source['elapsed_seconds'] = round(time.monotonic() - started, 3)
    write_json(out / (source_id + '.json'), source)
    return source


def validate(data, base):
    errors = check(data)
    if errors:
        return errors
    def fail(msg):
        errors.append(msg)
    def require(obj, fields, label):
        if not isinstance(obj, dict):
            fail(label + ': expected object')
            return False
        for field, kind in fields.items():
            if field not in obj or not isinstance(obj[field], kind):
                fail(label + ': invalid/missing ' + field)
        return True
    if not require(data, {'schema_version':str, 'run':dict, 'claims':list, 'sources':list,
                          'evidence':list, 'answer_items':list}, 'ledger'):
        return errors
    if errors:
        return errors
    if data['schema_version'] != '1.0':
        fail('unsupported schema version')
    run = data['run']
    try:
        started_at = datetime.fromisoformat(run['started_at'])
        datetime.fromisoformat(run['as_of'])
        if started_at.tzinfo is None:
            fail('run.started_at requires timezone')
    except ValueError:
        started_at = None
        fail('run: invalid timestamp')
    require(run, {'mode':str, 'input':str, 'started_at':str, 'as_of':str, 'case_nature':str,
                  'scope':str, 'scope_match':str, 'human_review':dict, 'semantic_review':dict,
                  'limitations':list, 'model':str}, 'run')
    if run.get('mode') not in ('live', 'replay'):
        fail('invalid mode')
    if run.get('scope_match') not in ('within', 'partial', 'outside'):
        fail('invalid scope match')
    if run.get('case_nature') not in ('reported_real_incident', 'hypothetical', 'exercise', 'unverified'):
        fail('invalid case nature')
    for field in ('human_review', 'semantic_review'):
        review = run.get(field, {})
        if isinstance(review, dict):
            require(review, {'status':str, 'reviewer':str, 'notes':str}, field)
            if review.get('status') not in ('pending', 'passed', 'failed'):
                fail('invalid review status: ' + field)
            if review.get('status') == 'passed' and not review.get('reviewer', '').strip():
                fail('passed review requires reviewer: ' + field)
    maps = {}
    for collection, key in [('claims','claim_id'), ('sources','source_id'), ('evidence','evidence_id')]:
        maps[collection] = {}
        for obj in data[collection]:
            if not isinstance(obj, dict) or not isinstance(obj.get(key), str) or not ID.fullmatch(obj[key]):
                fail(collection + ': invalid ID')
                continue
            if obj[key] in maps[collection]:
                fail(collection + ': duplicate ID ' + obj[key])
            maps[collection][obj[key]] = obj
    texts = {}
    for sid, s in maps['sources'].items():
        try:
            p = urlsplit(s['url'])
            if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.query or p.fragment:
                fail(sid + ': unsafe source URL')
            fetched_at = datetime.fromisoformat(s['retrieved_at'])
            if fetched_at.tzinfo is None:
                fail(sid + ': retrieved_at requires timezone')
            if run['mode']=='live' and s['access_status'] in ('read','partial') and started_at is not None:
                if fetched_at.tzinfo is not None and started_at.tzinfo is not None and fetched_at < started_at:
                    fail(sid + ': Live cannot use an earlier Replay snapshot as fresh retrieval')
            for key in ('published_at','updated_at','event_time'):
                if s[key] is not None:
                    datetime.fromisoformat(s[key])
        except ValueError:
            fail(sid + ': invalid date or URL')
        require(s, {'title':str, 'url':str, 'publisher':str, 'published_at':(str,type(None)),
                    'updated_at':(str,type(None)), 'event_time':(str,type(None)), 'retrieved_at':str,
                    'source_type':str, 'source_chain_id':str, 'derived_from':list,
                    'relationship':str, 'interests':str, 'access_status':str, 'read_scope':str,
                    'snapshot':(dict,type(None))}, sid)
        if s.get('access_status') not in ('read','partial','failed','restricted'):
            fail(sid + ': invalid access status')
        if not isinstance(s.get('source_chain_id'), str) or not s['source_chain_id'].strip():
            fail(sid + ': invalid source chain ID')
        if s.get('source_type') == 'republication' and not s.get('derived_from'):
            fail(sid + ': republication requires a parent source')
        for parent in s.get('derived_from', []):
            if not isinstance(parent, str) or parent not in maps['sources']:
                fail(sid + ': unknown parent source')
            elif parent == sid:
                fail(sid + ': source cannot derive from itself')
            elif s.get('source_chain_id') != maps['sources'][parent].get('source_chain_id'):
                fail(sid + ': derived source must keep the parent source chain')
        snap = s.get('snapshot')
        if s.get('access_status') in ('read','partial'):
            if not isinstance(snap, dict):
                fail(sid + ': missing snapshot')
                continue
            try:
                blob = contained(base, snap['path']).read_bytes()
                if digest(blob) != snap['sha256']:
                    fail(sid + ': snapshot hash mismatch')
                texts[sid] = blob.decode('utf-8')
                if 'raw_path' in snap:
                    if digest(contained(base, snap['raw_path']).read_bytes()) != snap['raw_sha256']:
                        fail(sid + ': raw snapshot hash mismatch')
            except (OSError, ValueError, KeyError, TypeError):
                fail(sid + ': unreadable snapshot')
    for cid, c in maps['claims'].items():
        if not c['text'].strip() or not c['reason'].strip():
            fail(cid + ': claim and reason cannot be empty')
        for sid in c['correction']['source_ids']:
            if sid not in maps['sources']:
                fail(cid + ': invalid correction source')
        require(c, {'text':str, 'entities':list, 'time':(str,type(None)), 'scope':str,
                    'status':str, 'evidence_ids':list, 'reason':str, 'limitations':list,
                    'statement_attribution':(str,type(None)), 'correction':dict}, cid)
        if c.get('status') not in STATES:
            fail(cid + ': invalid status')
        eids = c.get('evidence_ids', [])
        if not isinstance(eids, list):
            continue
        if c.get('status') != 'insufficient_evidence' and not eids:
            fail(cid + ': non-insufficient claim needs evidence')
        relations = set()
        for eid in eids:
            e = maps['evidence'].get(eid) if isinstance(eid,str) else None
            if not e or e.get('claim_id') != cid:
                fail(cid + ': invalid evidence link')
            elif isinstance(e.get('relation'),str):
                relations.add(e['relation'])
        if c.get('status') == 'supported' and 'supports' not in relations:
            fail(cid + ': supported needs supporting evidence')
        if c.get('status') == 'contradicted' and 'contradicts' not in relations:
            fail(cid + ': contradicted needs contrary evidence')
        if c.get('status') == 'disputed' and not {'supports','contradicts'} <= relations:
            fail(cid + ': disputed needs both directions')
    for eid, e in maps['evidence'].items():
        require(e, {'claim_id':str, 'source_id':str, 'quote':str, 'locator':dict,
                    'relation':str, 'extent':str}, eid)
        c = maps['claims'].get(e.get('claim_id')) if isinstance(e.get('claim_id'),str) else None
        if not c or eid not in c.get('evidence_ids', []):
            fail(eid + ': missing reciprocal claim link')
        if e.get('relation') not in ('supports','contradicts','context'):
            fail(eid + ': invalid relation')
        text = texts.get(e.get('source_id')) if isinstance(e.get('source_id'),str) else None
        loc = e.get('locator', {})
        if not isinstance(loc,dict):
            continue
        start, end = loc.get('start'), loc.get('end')
        if (text is None or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(text) or not e.get('quote')
                or text[start:end] != e.get('quote')):
            fail(eid + ': quote/location does not match captured text')
    for index, item in enumerate(data['answer_items']):
        label = 'answer ' + str(index)
        if not require(item, {'text':str,'claim_ids':list,'evidence_ids':list}, label):
            continue
        cids, eids = item.get('claim_ids'), item.get('evidence_ids')
        if not isinstance(cids,list) or not isinstance(eids,list):
            continue
        if not cids:
            fail(label + ': answer must reference a claim')
        allowed = set()
        for cid in cids:
            c = maps['claims'].get(cid) if isinstance(cid,str) else None
            if c is None:
                fail(label + ': unknown claim')
            elif isinstance(c.get('evidence_ids'),list):
                allowed.update(x for x in c['evidence_ids'] if isinstance(x,str))
        for eid in eids:
            if not isinstance(eid,str) or eid not in allowed:
                fail(label + ': evidence does not belong to cited claims')
        if allowed and not eids:
            fail(label + ': evidence citation required')
        for cid in cids:
            c = maps['claims'].get(cid)
            if c and c['evidence_ids'] and not set(c['evidence_ids']).intersection(eids):
                fail(label + ': each evidenced claim needs its own citation')
    if run.get('scope_match') == 'outside' and any(c.get('status') != 'insufficient_evidence' for c in maps['claims'].values()):
        fail('outside-scope replay cannot assert case answers')
    return errors


def source_chain_summary(data, claim_id):
    """Count cited documents and declared provenance chains for one claim.

    This reports ledger metadata; it does not infer whether publishers are truly
    independent or whether the metadata is complete.
    """
    sources = {s['source_id']: s for s in data['sources']}
    source_ids = []
    for evidence in data['evidence']:
        if evidence['claim_id'] == claim_id and evidence['source_id'] not in source_ids:
            source_ids.append(evidence['source_id'])
    chain_ids = []
    for source_id in source_ids:
        chain_id = sources[source_id]['source_chain_id']
        if chain_id not in chain_ids:
            chain_ids.append(chain_id)
    return {'source_ids': source_ids, 'source_chain_ids': chain_ids}


def quote_record(base, source, claim_id, evidence_id, start, end, relation, extent):
    text = contained(base, source['snapshot']['path']).read_bytes().decode('utf-8')
    if not 0 <= start < end <= len(text):
        raise ValueError('invalid quote offsets')
    return dict(evidence_id=evidence_id, claim_id=claim_id, source_id=source['source_id'],
                quote=text[start:end], locator=dict(start=start,end=end), relation=relation, extent=extent)


def md(text):
    # Source text is data, never executable HTML/Markdown injected into reports.
    return str(text).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('[','\\[').replace(']','\\]').replace('`','\\`')


def render(data):
    run = data['run']
    lines = ['# AI Incident Relay 核查报告', '', '模式：' + run['mode'], '',
             ('基于截至 ' + run['as_of'] + ' 保存的来源快照运行，本次未重新联网检索。'
              if run['mode'] == 'replay' else '本次为 Live；获取失败和未支持的内容不作为证据。'), '',
             '案例性质：' + run['case_nature'], '',
             '人工审核：' + run['human_review']['status'] + '；语义复核：' + run['semantic_review']['status'], '',
             '程序检查通过仅表示结构及引用匹配，不表示事实已获人工确认。', '',
             '## 输入', '', md(run['input']), '', '## 简明结论', '']
    for item in data['answer_items']:
        links = ['[' + x + '](#' + x.lower() + ')' for x in item['claim_ids'] + item['evidence_ids']]
        lines.extend([md(item['text']) + ' ' + ' '.join(links), ''])
    lines += ['## 主张与判断', '']
    for c in data['claims']:
        chain_summary = source_chain_summary(data, c['claim_id'])
        lines += ['### ' + c['claim_id'], '', md(c['text']), '',
                  '**' + c['status'] + '** — ' + md(c['reason']), '',
                  '归属：' + md(c['statement_attribution'] or '未归属机构声明'), '',
                  '证据计数：来源文档 ' + str(len(chain_summary['source_ids'])) +
                  '；声明的来源链 ' + str(len(chain_summary['source_chain_ids'])) +
                  '。同一来源链中的转载或派生材料不构成额外独立确认。', '',
                  '限制：' + md('；'.join(c['limitations']) or '无另列限制'), '',
                  '更正记录：' + md(json.dumps(c['correction'], ensure_ascii=False)), '']
    lines += ['## 证据', '']
    smap = {s['source_id']:s for s in data['sources']}
    for e in data['evidence']:
        s = smap[e['source_id']]
        # Do not embed full snapshots in public output; excerpts only.
        lines += ['### ' + e['evidence_id'], '', '主张：' + e['claim_id'] + '；方向：' + e['relation'], '',
                  '> ' + md(e['quote']).replace('\n','\n> '), '', md(e['extent']), '',
                  '来源：' + md(s['title']) + '（' + e['source_id'] + '）', '',
                  '定位：字符 ' + str(e['locator']['start']) + '–' + str(e['locator']['end']), '']
    lines += ['## 来源', '']
    for s in data['sources']:
        p = urlsplit(s['url'])
        safe_url = p.scheme in ('https','http') and p.hostname and not p.username and not p.password and not p.query and not p.fragment and not any(x in s['url'] for x in ('\n','\r','<','>'))
        link = '[原文](<' + s['url'] + '>)' if safe_url else 'URL 未公开：格式或敏感参数限制'
        lines += ['- ' + s['source_id'] + '：' + md(s['title']) + ' ' + link,
                  '  发布方：' + md(s['publisher']) + '；获取：' + md(s['retrieved_at']) +
                  '；状态：' + s['access_status'] + '；来源链：' + md(s['source_chain_id'])]
    lines += ['', '## 限制', ''] + ['- ' + md(x) for x in run['limitations']]
    return '\n'.join(lines) + '\n'


def export(ledger, out):
    started = time.monotonic()
    ledger = Path(ledger)
    data = read_json(ledger)
    errors = validate(data, ledger.parent)
    if not isinstance(data,dict):
        data={}
    run=data.get('run') if isinstance(data.get('run'),dict) else {}
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    review=run.get('semantic_review') if isinstance(run.get('semantic_review'),dict) else {}
    semantic_ok = review.get('status') == 'passed'
    record = dict(run_id=str(uuid.uuid4()), started_at=now(), mode=run.get('mode'),
                  ledger_sha256=digest(ledger.read_bytes()), code_sha256=digest(Path(__file__).read_bytes()),
                  structural_pass=not errors, semantic_review_pass=semantic_ok, errors=errors,
                  elapsed_seconds=round(time.monotonic()-started, 6),
                  log_scope='Python export only; host tool calls must be retained separately')
    write_json(out / 'validation.json', record)
    if errors or not semantic_ok:
        # Source-only partial result; never include invalid draft assertions as conclusions.
        available=[]
        for s in data.get('sources',[]) if isinstance(data.get('sources'),list) else []:
            try:
                snap=s['snapshot']
                if s['access_status'] in ('read','partial') and digest(contained(ledger.parent,snap['path']).read_bytes())==snap['sha256']:
                    u=urlsplit(s['url'])
                    if u.scheme in ('https','http') and u.hostname and not u.username and not u.password and not u.query and not u.fragment:
                        available.append({'source_id':s['source_id'],'title':s['title'],'url':s['url'],
                                          'access_status':s['access_status'],'snapshot_sha256':snap['sha256']})
            except (KeyError,TypeError,ValueError,OSError):
                continue
        write_json(out / 'available-sources.json',available)
        write_json(out / 'failure.json', dict(status='blocked', errors=errors,
                   limitations=['No validated final answer exported.', 'Review source ledger locally; do not treat draft assertions as verified.']))
        (out / 'report.md').write_text('# 核查未通过\n\n未导出确定结论。请查看 validation.json；原始账本保留在本地。\n', encoding='utf-8')
        return False
    for source in data['sources']:
        snap = source.get('snapshot')
        if not snap:
            continue
        for path_key in ('path','raw_path'):
            if path_key in snap:
                original = contained(ledger.parent,snap[path_key])
                relative = 'snapshots/' + source['source_id'] + ('.txt' if path_key=='path' else '.raw')
                target = contained(out,relative)
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(original,target)
                snap[path_key] = relative
    write_json(out / 'ledger.json', data)
    (out / 'report.md').write_text(render(data), encoding='utf-8')
    record['elapsed_seconds']=round(time.monotonic()-started,6)
    record['exported_ledger_sha256']=digest((out/'ledger.json').read_bytes())
    record['report_sha256']=digest((out/'report.md').read_bytes())
    write_json(out/'validation.json',record)
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    cap = sub.add_parser('capture')
    for name in ('url','out','source-id','title','publisher'):
        cap.add_argument('--'+name, required=True)
    val = sub.add_parser('validate')
    val.add_argument('ledger')
    exp = sub.add_parser('export')
    exp.add_argument('ledger')
    exp.add_argument('--out', required=True)
    q = sub.add_parser('quote')
    q.add_argument('source')
    q.add_argument('--claim-id',required=True)
    q.add_argument('--evidence-id',required=True)
    q.add_argument('--start',type=int,required=True)
    q.add_argument('--end',type=int,required=True)
    q.add_argument('--relation',choices=['supports','contradicts','context'],required=True)
    q.add_argument('--extent',required=True)
    args = p.parse_args()
    try:
        if args.command == 'capture':
            result = capture(args.url,args.out,args.source_id,args.title,args.publisher)
            print(json.dumps({'source_id':result['source_id'],'access_status':result['access_status']},ensure_ascii=False))
            return 0 if result['access_status']=='read' else 2
        if args.command == 'validate':
            errors = validate(read_json(args.ledger),Path(args.ledger).parent)
            print(json.dumps({'passed':not errors,'errors':errors},ensure_ascii=False,indent=2))
            return 1 if errors else 0
        if args.command == 'export':
            return 0 if export(args.ledger,args.out) else 1
        if args.command == 'quote':
            print(json.dumps(quote_record(Path(args.source).parent,read_json(args.source),args.claim_id,
                  args.evidence_id,args.start,args.end,args.relation,args.extent),ensure_ascii=False,indent=2))
            return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status':'failed','error_type':type(exc).__name__,'message':'Invalid input or unavailable output. No fallback used.'}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
