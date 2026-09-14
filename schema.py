"""Small, explicit JSON Schema subset; no third-party dependency."""
import json
from pathlib import Path


def string(enum=None, nullable=False):
    d = {'type': ['string', 'null'] if nullable else 'string'}
    if enum is not None:
        d['enum'] = enum
    return d


def array(items):
    return {'type':'array', 'items':items}


def obj(properties, optional=()):
    return {'type':'object', 'properties':properties,
            'required':[k for k in properties if k not in optional]}


S = string()
N = string(nullable=True)
SS = array(S)
REVIEW = obj({'status':string(['pending','passed','failed']), 'reviewer':S, 'notes':S})
SNAPSHOT = obj({'path':S,'sha256':S,'extractor':S,'raw_path':S,'raw_sha256':S},('raw_path','raw_sha256'))
SNAPSHOT['type'] = ['object','null']
SCHEMA = obj({
    'schema_version':string(['1.0']),
    'run':obj({'mode':string(['live','replay']), 'input':S, 'started_at':S,'as_of':S,
               'case_nature':string(['reported_real_incident','hypothetical','exercise','unverified']),
               'scope':S,'scope_match':string(['within','partial','outside']),
               'human_review':REVIEW,'semantic_review':REVIEW,'limitations':SS,'model':S}),
    'claims':array(obj({'claim_id':S,'text':S,'entities':SS,'time':N,'scope':S,
                        'status':string(['supported','contradicted','disputed','insufficient_evidence','inference']),
                        'evidence_ids':SS,'reason':S,'limitations':SS,'statement_attribution':N,
                        'correction':obj({'status':string(['none_known','updated','corrected','unverified']),
                                          'source_ids':SS,'notes':S})})),
    'sources':array(obj({'source_id':S,'title':S,'url':S,'publisher':S,
                         'published_at':N,'updated_at':N,'event_time':N,'retrieved_at':S,'time_notes':S,
                         'source_type':string(['primary_disclosure','technical_report','independent_investigation','republication','public_record','test_fixture']),
                         'source_chain_id':S,'derived_from':SS,'relationship':S,'interests':S,
                         'access_status':string(['read','partial','failed','restricted']),
                         'read_scope':S,'snapshot':SNAPSHOT})),
    'evidence':array(obj({'evidence_id':S,'claim_id':S,'source_id':S,'quote':S,
                          'locator':obj({'start':{'type':'integer'},'end':{'type':'integer'}}),
                          'relation':string(['supports','contradicts','context']),'extent':S})),
    'answer_items':array(obj({'text':S,'claim_ids':SS,'evidence_ids':SS}))
})
SCHEMA['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
SCHEMA['title'] = 'AI Incident Relay ledger 1.0'


def check(value, rule=SCHEMA, path='$'):
    errors = []
    kinds = rule.get('type', [])
    if isinstance(kinds,str):
        kinds = [kinds]
    types = {'object':dict,'array':list,'string':str,'integer':int,'null':type(None),'boolean':bool}
    if kinds and not any(type(value) is types[k] for k in kinds):
        return [path + ': invalid data type']
    if 'enum' in rule and value not in rule['enum']:
        errors.append(path + ': invalid enum value')
    if isinstance(value,dict):
        for field in rule.get('required',[]):
            if field not in value:
                errors.append(path + ': missing ' + field)
        for field, child in rule.get('properties',{}).items():
            if field in value:
                errors.extend(check(value[field],child,path+'.'+field))
    if isinstance(value,list):
        for i, child in enumerate(value):
            errors.extend(check(child,rule['items'],path+'['+str(i)+']'))
    return errors


if __name__ == '__main__':
    Path(__file__).with_name('ledger.schema.json').write_text(
        json.dumps(SCHEMA,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
