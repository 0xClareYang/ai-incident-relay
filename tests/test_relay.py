import copy
import ipaddress
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import relay


def fixture(base):
    text='TEST FIXTURE. The service affected five test datasets. This is synthetic, not a real incident.\n'
    (base/'source.txt').write_bytes(text.encode('utf-8'))
    source=dict(source_id='S1',title='Synthetic test source',url='https://example.org/test',publisher='Test fixture',
                published_at=None,updated_at=None,event_time=None,retrieved_at=relay.now(),time_notes='Synthetic',
                source_type='test_fixture',source_chain_id='chain1',derived_from=[],relationship='Synthetic',
                interests='None; synthetic fixture',access_status='read',read_scope='entire synthetic fixture',
                snapshot=dict(path='source.txt',sha256=relay.digest(text.encode()),extractor='fixture'))
    evidence=relay.quote_record(base,source,'C1','E1',14,53,'supports','Synthetic limited scope')
    return dict(schema_version='1.0',run=dict(mode='replay',input='TEST INPUT',started_at=relay.now(),
                as_of='2026-09-14',case_nature='hypothetical',scope='synthetic data',scope_match='within',model='test fixture',
                human_review=dict(status='pending',reviewer='',notes='Synthetic fixture'),
                semantic_review=dict(status='passed',reviewer='test fixture',notes='Test only'),limitations=['Synthetic']),
                claims=[dict(claim_id='C1',text='Five test datasets were affected.',entities=['test service'],time=None,
                    scope='test datasets',status='supported',evidence_ids=['E1'],reason='Test text',limitations=[],
                    statement_attribution='test source',correction=dict(status='none_known',source_ids=[],notes='Test'))],
                sources=[source],evidence=[evidence],answer_items=[dict(text='According to synthetic source, five test datasets were affected.',claim_ids=['C1'],evidence_ids=['E1'])])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.data=fixture(self.base)
    def tearDown(self):
        self.temp.cleanup()
    def errors(self):
        return relay.validate(self.data,self.base)
    def test_valid(self):
        self.assertEqual(self.errors(),[])
    def test_non_object_export_fails_closed(self):
        relay.write_json(self.base/'ledger.json',[])
        self.assertFalse(relay.export(self.base/'ledger.json',self.base/'out'))
        self.assertFalse((self.base/'out/ledger.json').exists())
    def test_malformed_run_export_fails_closed(self):
        self.data['run']=[]
        self.data['sources']=42
        relay.write_json(self.base/'ledger.json',self.data)
        self.assertFalse(relay.export(self.base/'ledger.json',self.base/'out'))
    def test_duplicate_json_key_rejected(self):
        (self.base/'bad.json').write_text('{"a":1,"a":2}',encoding='utf-8')
        with self.assertRaises(ValueError):
            relay.read_json(self.base/'bad.json')
    def test_nonfinite_json_rejected(self):
        (self.base/'bad.json').write_text('{"a":NaN}',encoding='utf-8')
        with self.assertRaises(ValueError):
            relay.read_json(self.base/'bad.json')
    def test_live_rejects_old_snapshot(self):
        self.data['run']['mode']='live'
        self.data['sources'][0]['retrieved_at']='2020-01-01T00:00:00+00:00'
        self.assertTrue(self.errors())
    def test_crlf_offsets_preserved(self):
        text='header\r\nfive datasets\r\n'
        (self.base/'source.txt').write_bytes(text.encode())
        source=self.data['sources'][0]
        source['snapshot']['sha256']=relay.digest(text.encode())
        self.data['evidence'][0]=relay.quote_record(self.base,source,'C1','E1',8,21,'supports','Test')
        self.assertEqual(self.data['evidence'][0]['quote'],'five datasets')
        self.assertEqual(self.errors(),[])
    def test_missing_field(self):
        del self.data['claims'][0]['scope']
        self.assertTrue(self.errors())
    def test_bad_type_does_not_crash(self):
        for path in [('claims',0,'evidence_ids'),('sources',0,'derived_from'),('evidence',0,'source_id')]:
            value=copy.deepcopy(self.data)
            value[path[0]][path[1]][path[2]]={}
            self.assertTrue(relay.validate(value,self.base))
    def test_invalid_id(self):
        self.data['answer_items'][0]['evidence_ids']=['MISSING']
        self.assertTrue(self.errors())
    def test_duplicate_id(self):
        self.data['sources'].append(copy.deepcopy(self.data['sources'][0]))
        self.assertTrue(self.errors())
    def test_fabricated_quote(self):
        self.data['evidence'][0]['quote']='Everyone was affected'
        self.assertTrue(self.errors())
    def test_wrong_offset(self):
        self.data['evidence'][0]['locator']['start']+=1
        self.assertTrue(self.errors())
    def test_bool_offset_rejected(self):
        self.data['evidence'][0]['locator']['start']=True
        self.assertTrue(self.errors())
    def test_tampered_snapshot(self):
        (self.base/'source.txt').write_text('modified',encoding='utf-8')
        self.assertTrue(self.errors())
    def test_snapshot_escape(self):
        self.data['sources'][0]['snapshot']['path']='../private.txt'
        self.assertTrue(self.errors())
    def test_insufficient_allowed_without_evidence(self):
        self.data['claims'][0].update(status='insufficient_evidence',evidence_ids=[])
        self.data['evidence']=[]
        self.data['answer_items'][0]['evidence_ids']=[]
        self.assertEqual(self.errors(),[])
    def test_supported_requires_evidence(self):
        self.data['claims'][0]['evidence_ids']=[]
        self.assertTrue(self.errors())
    def test_disputed_requires_both_directions(self):
        self.data['claims'][0]['status']='disputed'
        self.assertTrue(self.errors())
    def test_outside_scope_cannot_assert_case_answer(self):
        self.data['run']['scope_match']='outside'
        self.assertTrue(self.errors())
    def test_unknown_correction_source(self):
        self.data['claims'][0]['correction']['source_ids']=['UNKNOWN']
        self.assertTrue(self.errors())
    def test_unknown_parent(self):
        self.data['sources'][0]['derived_from']=['UNKNOWN']
        self.assertTrue(self.errors())
    def test_unsafe_url(self):
        self.data['sources'][0]['url']='https://example.org/?token=not-a-real-secret'
        self.assertTrue(self.errors())
    def test_failed_source_cannot_supply_evidence(self):
        self.data['sources'][0]['access_status']='failed'
        self.data['sources'][0]['snapshot']=None
        self.assertTrue(self.errors())
    def test_semantic_pending_blocks_export(self):
        self.data['run']['semantic_review']['status']='pending'
        relay.write_json(self.base/'ledger.json',self.data)
        self.assertFalse(relay.export(self.base/'ledger.json',self.base/'out'))
        self.assertFalse((self.base/'out'/'ledger.json').exists())
    def test_export_portable_and_human_pending_visible(self):
        relay.write_json(self.base/'ledger.json',self.data)
        self.assertTrue(relay.export(self.base/'ledger.json',self.base/'out'))
        exported=relay.read_json(self.base/'out'/'ledger.json')
        self.assertEqual(relay.validate(exported,self.base/'out'),[])
        self.assertIn('人工审核：pending',(self.base/'out'/'report.md').read_text(encoding='utf-8'))
        with self.assertRaises(FileExistsError):
            relay.export(self.base/'ledger.json',self.base/'out')
    def test_meaning_is_not_programmatically_certified(self):
        self.data['answer_items'][0]['text']='All users everywhere were affected.'
        self.assertEqual(self.errors(),[])  # Deliberate semantic gap: requires model/human review.
    def test_no_fact_without_claim(self):
        self.data['answer_items'][0]['claim_ids']=[]
        self.assertTrue(self.errors())
    def test_html_injection_escaped(self):
        self.data['answer_items'][0]['text']='<script>alert(1)</script> ![x](https://example.org)'
        report=relay.render(self.data)
        self.assertNotIn('<script>',report)
        self.assertNotIn('![x]',report)


class FetchTests(unittest.TestCase):
    def test_block_local_ipv4_ipv6_and_mixed_dns(self):
        for addresses in [['127.0.0.1'],['10.0.0.1'],['169.254.169.254'],['::1'],['::ffff:127.0.0.1'],['64:ff9b::a00:1'],['2002:7f00:1::'],['93.184.216.34','192.168.1.1']]:
            results=[(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443)) for ip in addresses]
            with patch('socket.getaddrinfo',return_value=results):
                with self.assertRaises(ValueError):
                    relay.url_target('https://example.org/')
    def test_protocol_port_credentials_query(self):
        for url in ['file:///etc/passwd','ftp://example.org/','http://example.org:8080/',
                    'https://user:pass@example.org/','https://example.org/?token=fixture','https://example.org/\nattack']:
            with self.assertRaises(ValueError):
                relay.url_target(url)
    def test_pinned_tls_connection_does_not_reresolve_hostname(self):
        conn=relay.PinnedHTTPS('example.org',443,'93.184.216.34')
        context=MagicMock()
        conn._context=context
        with patch('socket.create_connection') as connect:
            conn.connect()
            self.assertEqual(connect.call_args.args[0],('93.184.216.34',443))
            self.assertEqual(context.wrap_socket.call_args.kwargs['server_hostname'],'example.org')
    def test_capture_failure_is_not_replay(self):
        with tempfile.TemporaryDirectory() as tmp, patch('relay.fetch',side_effect=TimeoutError):
            source=relay.capture('https://example.org/',tmp,'S1','Test','Test')
            self.assertEqual(source['access_status'],'failed')
            self.assertIsNone(source['snapshot'])
            self.assertFalse((Path(tmp)/'S1.txt').exists())
    def test_redirect_to_private_blocked(self):
        response=MagicMock(status=302)
        response.getheader.return_value='https://127.0.0.1/'
        conn=MagicMock()
        conn.getresponse.return_value=response
        def addresses(host,port,**kwargs):
            return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1' if host=='127.0.0.1' else '93.184.216.34',port))]
        with patch('socket.getaddrinfo',side_effect=addresses),patch('relay.PinnedHTTPS',return_value=conn):
            with self.assertRaisesRegex(ValueError,'non-public'):
                relay.fetch('https://example.org/')
    def test_size_and_compression_rejected(self):
        for headers in [{'Content-Type':'text/plain','Content-Length':str(relay.MAX_BYTES+1)},
                        {'Content-Type':'text/plain','Content-Encoding':'gzip'},
                        {'Content-Type':'application/pdf'}]:
            response=MagicMock(status=200)
            response.getheader.side_effect=lambda k,default=None:headers.get(k,default)
            conn=MagicMock()
            conn.getresponse.return_value=response
            with patch('relay.url_target',return_value=(relay.urlsplit('https://example.org'), 'example.org',443,'93.184.216.34')),patch('relay.PinnedHTTPS',return_value=conn):
                with self.assertRaises(ValueError):
                    relay.fetch('https://example.org')
    def test_script_text_not_executed_or_extracted(self):
        parser=relay.TextExtractor()
        parser.feed('<p>source fact</p><script>evil()</script><p>next fact</p>')
        self.assertEqual(parser.text(),'source fact\nnext fact\n')


if __name__=='__main__':
    unittest.main()
