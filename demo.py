"""Reproduce a clearly labelled test draft failure then corrected Replay export."""
import copy
import shutil
from pathlib import Path
import relay


def main():
    case = relay.ROOT/'cases'/'hf-demo-public'
    run = relay.ROOT/'runs'/('replay-demo-'+relay.now().replace(':','-'))
    run.mkdir(parents=True)
    shutil.copytree(case/'snapshots',run/'snapshots')
    ledger = relay.read_json(case/'ledger.json')
    ledger['run']['started_at'] = relay.now()
    bad = copy.deepcopy(ledger)
    bad['run']['input'] += '\n【测试草稿】以下引文错误由开发者故意注入，不是普通 AI 自然生成的错误。'
    bad['evidence'][0]['quote'] = 'TEST DRAFT: fabricated quotation for checker demonstration.'
    relay.write_json(run/'test-draft.json',bad)
    if relay.export(run/'test-draft.json',run/'01-rejected'):
        raise RuntimeError('The intentionally invalid draft unexpectedly passed')
    relay.write_json(run/'ledger.json',ledger)
    if not relay.export(run/'ledger.json',run/'02-corrected'):
        raise RuntimeError('Corrected replay failed validation')
    relay.write_json(run/'sequence.json',dict(mode='replay',test_input=True,injected_draft=True,
                     revision_count=1,network_requests=0,case_human_review='pending',
                     steps=['load saved case','inject labelled test quote','actual checker rejects',
                            'restore captured quotation','actual checker/export passes'],
                     limitation='Fixture reproduction; no new model semantic investigation in this script.'))
    print(run)


if __name__=='__main__':
    main()
