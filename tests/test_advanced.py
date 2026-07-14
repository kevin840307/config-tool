from copy import deepcopy
from pathlib import Path
import pytest
from pydantic import ValidationError
from yaml_config_engine import YamlPatchEngine, DiffCompiler
from yaml_config_engine.yamlio import make_yaml


def y(text): return make_yaml().load(text)

def test_readable_copy_shorthand():
    doc=y('db:\n  - version: "LONG-2025.6.123"\n    data: B\n')
    cfg={'variables':{'SRC':'LONG-2025.6.123','DST':'LONG-2025.4.999'},'operations':[{
        'op':'copy_item','target':'$.db','from':{'version':'{{ SRC }}'},'change':{'version':'{{ DST }}'},'after_source':True
    }]}
    out=YamlPatchEngine().apply_document(doc,cfg)
    assert [x['version'] for x in out['db']]==['LONG-2025.6.123','LONG-2025.4.999']

def test_readable_update_shorthand():
    doc=y('db:\n  - name: A\n    data: B\n')
    cfg={'operations':[{'op':'update_item','target':'$.db','find':{'name':'A'},'change':{'data':'C'},'expect_matches':1}]}
    assert YamlPatchEngine().apply_document(doc,cfg)['db'][0]['data']=='C'

def test_matcher_extended_operators():
    doc=y('items:\n  - name: FAB14-ABC\n    n: 15\n')
    cfg={'operations':[{'op':'update_item','path':'$.items','match':{'name':{'$starts_with':'FAB14'},'n':{'$between':[10,20]}},'set':{'ok':True},'expect_matches':1}]}
    assert YamlPatchEngine().apply_document(doc,cfg)['items'][0]['ok'] is True

def test_regex_and_type_match():
    doc=y('items:\n  - name: api-123\n    enabled: true\n')
    cfg={'operations':[{'op':'update_item','path':'$.items','match':{'name':{'$regex':'^api-\\d+$'},'enabled':{'$type':'bool'}},'set':{'x':1},'expect_matches':1}]}
    assert YamlPatchEngine().apply_document(doc,cfg)['items'][0]['x']==1

@pytest.mark.parametrize('strategy,expected',[('append',[1,2,3]),('prepend',[2,3,1]),('unique',[1,2,3])])
def test_merge_array_strategies(strategy,expected):
    doc=y('x: [1]\n')
    value=[2,3] if strategy!='unique' else [1,2,3]
    out=YamlPatchEngine().apply_document(doc,{'operations':[{'op':'merge','path':'$.x','strategy':strategy,'value':value}]})
    assert list(out['x'])==expected

def test_merge_keep_existing_and_delete_null():
    doc=y('x:\n  a: 1\n  b: 2\n')
    out=YamlPatchEngine().apply_document(doc,{'operations':[{'op':'merge','path':'$.x','strategy':'keep_existing','value':{'a':9,'c':3}},{'op':'merge','path':'$.x','strategy':'delete_null','value':{'b':None}}]})
    assert out['x']=={'a':1,'c':3}

def test_copy_and_move_node():
    doc=y('a:\n  x: 1\nb: {}\n')
    cfg={'operations':[{'op':'copy_node','from_path':'$.a','to_path':'$.b.copy'},{'op':'move_node','from_path':'$.a.x','to_path':'$.b.moved'}]}
    out=YamlPatchEngine().apply_document(doc,cfg)
    assert out['b']['copy']['x']==1 and out['b']['moved']==1 and out['a']=={}

def test_copy_key_position():
    doc=y('x:\n  a: 1\n  c: 3\n')
    cfg={'operations':[{'op':'copy_key','path':'$.x','source_key':'a','target_key':'b','position':{'before_key':'c'}}]}
    out=YamlPatchEngine().apply_document(doc,cfg)
    assert list(out['x'])==['a','b','c']

def test_document_selector(tmp_path):
    src=tmp_path/'k.yaml'; src.write_text('kind: Service\nname: s\n---\nkind: Deployment\nname: d\n',encoding='utf-8')
    cfg={'documents':{'match':{'kind':'Deployment'}},'operations':[{'op':'set','path':'$.name','value':'changed'}]}
    result=YamlPatchEngine().apply_file(src,cfg)
    assert result.documents[0]['name']=='s' and result.documents[1]['name']=='changed'

def test_config_validation_rejects_bad_copy():
    with pytest.raises(ValidationError):
        YamlPatchEngine().apply_document({}, {'operations':[{'op':'copy_item','path':'$.x'}]})

def test_diff_mapping_add_remove_rename_and_order():
    a=y('x:\n  old: 1\n  keep: 2\n')
    b=y('x:\n  keep: 2\n  new: 1\n  extra: 3\n')
    r=DiffCompiler().compile(a,b)
    assert r.verified
    names=[op['op'] for op in r.config['operations']]
    assert 'rename_key' in names and 'insert_key' in names

def test_diff_array_reorder_generates_move():
    a=y('x:\n  - name: A\n  - name: B\n  - name: C\n')
    b=y('x:\n  - name: C\n  - name: A\n  - name: B\n')
    r=DiffCompiler().compile(a,b)
    assert r.verified and any(op['op']=='move_item' for op in r.config['operations'])

def test_diff_composite_identity_rule():
    a=y('x:\n  - app: api\n    region: tw\n    value: 1\n  - app: api\n    region: us\n    value: 2\n')
    b=y('x:\n  - app: api\n    region: tw\n    value: 9\n  - app: api\n    region: us\n    value: 2\n')
    r=DiffCompiler({'$.x':['app','region']}).compile(a,b)
    assert r.verified and any(op['op']=='update_item' for op in r.config['operations'])

def test_defaults_reduce_repetition():
    doc=y('x:\n  - name: A\n  - name: B\n')
    cfg={'defaults':{'path':'$.x','expect_matches':1},'operations':[{'op':'update_item','match':{'name':'A'},'set':{'v':1}},{'op':'update_item','match':{'name':'B'},'set':{'v':2}}]}
    out=YamlPatchEngine().apply_document(doc,cfg)
    assert [i['v'] for i in out['x']]==[1,2]

def _contains_generated_list_index(op):
    if op.get('op') not in {'insert','copy_item','move_item','update_item','remove_item'}:
        return False
    position=op.get('position',{})
    if 'index' in position:
        return True
    for side in ('before','after'):
        if isinstance(position.get(side),dict) and 'index' in position[side]:
            return True
    source=op.get('source',{})
    return isinstance(source,dict) and 'index' in source


def test_diff_list_insert_uses_relative_identity_not_index():
    a=y('x:\n  - name: A\n  - name: C\n')
    b=y('x:\n  - name: A\n  - name: B\n  - name: C\n')
    r=DiffCompiler().compile(a,b)
    assert r.verified
    assert not any(_contains_generated_list_index(op) for op in r.config['operations'])
    inserted=next(op for op in r.config['operations'] if op['op'] in {'insert','copy_item'})
    assert inserted['position']['after']['match']=={'name':'A'}


def test_diff_list_copy_uses_relative_identity_not_index():
    a=y('db:\n  - version: "2025.6"\n    data: B\n')
    b=y('db:\n  - version: "2025.6"\n    data: B\n  - version: "2025.4"\n    data: B\n')
    r=DiffCompiler().compile(a,b)
    assert r.verified
    copy_op=next(op for op in r.config['operations'] if op['op']=='copy_item')
    assert 'index' not in copy_op['position']
    assert copy_op['position']['after']['match']=={'version':'2025.6'}


def test_diff_list_move_uses_relative_identity_not_index():
    a=y('x:\n  - name: A\n  - name: B\n  - name: C\n')
    b=y('x:\n  - name: C\n  - name: A\n  - name: B\n')
    r=DiffCompiler().compile(a,b)
    assert r.verified
    moves=[op for op in r.config['operations'] if op['op']=='move_item']
    assert moves and not any(_contains_generated_list_index(op) for op in moves)


def test_folder_enterprise_allow_deny_filters(tmp_path):
    from yaml_config_engine.folder_compiler import FolderCompiler
    from yaml_config_engine.yamlio import dump_one, load_one

    before = tmp_path / 'before'
    after = tmp_path / 'after'
    for root, value in ((before, 1), (after, 2)):
        for app in ('eocap-app1', 'eocap-app2'):
            (root / 'FAB14-A' / 'STAGING' / app).mkdir(parents=True, exist_ok=True)
        (root / 'FAB14-A' / 'PROD' / 'eocap-app1').mkdir(parents=True, exist_ok=True)
        (root / 'FAB18-A' / 'STAGING' / 'eocap-app1').mkdir(parents=True, exist_ok=True)
        dump_one({'value': value}, root / 'FAB14-A' / 'STAGING' / 'eocap-app1' / 'application.yaml')
        dump_one({'value': value}, root / 'FAB14-A' / 'STAGING' / 'eocap-app2' / 'application.yaml')
        dump_one({'value': value}, root / 'FAB14-A' / 'PROD' / 'eocap-app1' / 'application.yaml')
        dump_one({'value': value}, root / 'FAB18-A' / 'STAGING' / 'eocap-app1' / 'application.yaml')
    out = tmp_path / 'generated'
    result = FolderCompiler().compile_folder(
        before, after, out,
        layout='expanded',
        path_allow=['eocap-app1/application.yaml', 'eocap-app2/application.yaml'],
        path_deny=['eocap-app2/**'],
        fab_allow_prefix=['FAB14'], fab_deny_prefix=['FAB14-Z'],
        env_allow=['STAGING'], env_deny=['DEV'],
    )
    assert result.verified
    manifest = load_one(out / 'manifest.yaml')
    paths = [x['relative_path'] for x in manifest['files']]
    assert paths == ['FAB14-A/STAGING/eocap-app1/application.yaml']
    assert manifest['filters']['path_allow'] == ['eocap-app1/application.yaml', 'eocap-app2/application.yaml']


def test_copy_item_with_dynamic_key_rename_is_compiled_semantically():
    from yaml_config_engine.diff_compiler import DiffCompiler
    before = {'db': [
        {'config-v508-abc': True, 'name': 'config-v508-777', 'data': 'A'},
        {'config-v506-abc': True, 'name': 'config-v506-777', 'data': 'A'},
    ]}
    after = {'db': [
        {'config-v512-abc': True, 'name': 'config-v512-777', 'data': 'A'},
        {'config-v508-abc': True, 'name': 'config-v508-777', 'data': 'A'},
    ]}
    result = DiffCompiler().compile(before, after)
    assert result.verified
    copy_ops = [x for x in result.config['operations'] if x['op'] == 'copy_item']
    assert len(copy_ops) == 1
    assert copy_ops[0]['item_operations'] == [
        {'op': 'rename_key', 'path': '$', 'old_key': 'config-v508-abc', 'new_key': 'config-v512-abc'}
    ]
    assert not any('index' in str(x) for x in result.config['operations'] if x['op'] in {'copy_item','insert','move_item'})

def test_copy_item_defaults_before_source():
    doc = y('db:\n  - version: "508"\n    data: A\n')
    cfg = {'operations': [{'op': 'copy_item', 'path': '$.db', 'source': {'match': {'version': '508'}}, 'set': {'version': '512'}}]}
    out = YamlPatchEngine().apply_document(doc, cfg)
    assert [x['version'] for x in out['db']] == ['512', '508']


def test_copy_item_default_can_be_overridden_globally():
    doc = y('db:\n  - version: "508"\n    data: A\n')
    cfg = {
        'defaults': {'copy_item_position': 'after_source'},
        'operations': [{'op': 'copy_item', 'path': '$.db', 'source': {'match': {'version': '508'}}, 'set': {'version': '512'}}],
    }
    out = YamlPatchEngine().apply_document(doc, cfg)
    assert [x['version'] for x in out['db']] == ['508', '512']


def test_rules_config_applies_different_changes_by_path_fab_env(tmp_path):
    from yaml_config_engine.folder_compiler import FolderCompiler
    from yaml_config_engine.yamlio import dump_one, load_one

    source = tmp_path / 'source'
    paths = [
        ('FAB14-A', 'STAGING', 'eocap-app1', 'A'),
        ('FAB14-A', 'STAGING', 'eocap-app2', 'A'),
        ('FAB14-A', 'PROD', 'eocap-app1', 'A'),
        ('FAB18-A', 'STAGING', 'eocap-app1', 'A'),
    ]
    for fab, env, app, value in paths:
        folder = source / fab / env / app
        folder.mkdir(parents=True, exist_ok=True)
        dump_one({'value': value, 'site': '{{ untouched }}'}, folder / 'application.yaml')

    config = {
        'version': 1,
        'rules': [
            {
                'id': 'app1-staging-add-A',
                'priority': 100,
                'filters': {
                    'path_allow': ['eocap-app1/application.yaml'],
                    'fab_allow_prefix': ['FAB14'],
                    'env_allow': ['STAGING'],
                },
                'operations': [{'op': 'set', 'path': '$.value', 'value': 'A-ADDED'}],
            },
            {
                'id': 'app2-staging-add-B',
                'filters': {
                    'path_allow': ['eocap-app2/application.yaml'],
                    'fab_allow_prefix': ['FAB14'],
                    'env_allow': ['STAGING'],
                },
                'operations': [{'op': 'set', 'path': '$.value', 'value': 'B-ADDED'}],
            },
        ],
    }
    cfg_path = tmp_path / 'rules.yaml'
    dump_one(config, cfg_path)
    output = tmp_path / 'output'
    report = FolderCompiler().apply_rules_config(source, cfg_path, output)

    assert load_one(output / 'FAB14-A/STAGING/eocap-app1/application.yaml')['value'] == 'A-ADDED'
    assert load_one(output / 'FAB14-A/STAGING/eocap-app2/application.yaml')['value'] == 'B-ADDED'
    assert load_one(output / 'FAB14-A/PROD/eocap-app1/application.yaml')['value'] == 'A'
    assert load_one(output / 'FAB18-A/STAGING/eocap-app1/application.yaml')['value'] == 'A'
    assert report['summary']['changed_files'] == 2


def test_rules_priority_stop_and_deny(tmp_path):
    from yaml_config_engine.folder_compiler import FolderCompiler
    from yaml_config_engine.yamlio import dump_one, load_one
    source = tmp_path / 'source'
    target = source / 'FAB14-A/STAGING/eocap-app1'
    target.mkdir(parents=True)
    dump_one({'steps': []}, target / 'application.yaml')
    config = {
        'rules': [
            {'id': 'low', 'priority': 1, 'filters': {'path_allow': ['**/application.yaml']},
             'operations': [{'op': 'append', 'path': '$.steps', 'value': 'LOW'}]},
            {'id': 'high-stop', 'priority': 100, 'stop': True,
             'filters': {'path_allow': ['eocap-app1/application.yaml'], 'env_allow': ['STAGING'], 'path_deny': ['**/backup/**']},
             'operations': [{'op': 'append', 'path': '$.steps', 'value': 'HIGH'}]},
        ]
    }
    output = tmp_path / 'output'
    FolderCompiler().apply_rules_config(source, config, output)
    assert load_one(output / 'FAB14-A/STAGING/eocap-app1/application.yaml')['steps'] == ['HIGH']
