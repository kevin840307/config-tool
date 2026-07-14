from copy import deepcopy
from pathlib import Path
from yaml_config_engine.engine import YamlPatchEngine
from yaml_config_engine.diff_compiler import DiffCompiler
from yaml_config_engine.folder_compiler import FolderCompiler
from yaml_config_engine.yamlio import dump_one, load_one


def test_place_shortcuts_for_mapping_and_list():
    doc={'a':1,'items':[{'name':'A'},{'name':'C'}]}
    cfg={'operations':[
        {'op':'insert_key','key':'top','value':0,'place':'top'},
        {'op':'insert_key','key':'b','value':2,'place':{'after_key':'a'}},
        {'op':'insert','path':'$.items','value':{'name':'B'},'place':{'after':{'name':'A'}}},
    ]}
    out=YamlPatchEngine().apply_document(doc,cfg)
    assert list(out)==['top','a','b','items']
    assert [x['name'] for x in out['items']]==['A','B','C']


def test_copy_node_with_mapping_position():
    doc={'version':'1','db':[{'version':'2025.6'}],'shadow':False}
    cfg={'operations':[{'op':'copy_node','from_path':'$.db','to_path':'$.new-setting','place':{'after_key':'version'}}]}
    out=YamlPatchEngine().apply_document(doc,cfg)
    assert list(out)==['version','new-setting','db','shadow']
    assert out['new-setting']==out['db']
    assert out['new-setting'] is not out['db']


def test_copy_item_to_node_with_position():
    doc={'version':'1','db':[{'version':'2025.6','data':'A'},{'version':'2024.6','data':'B'}],'shadow':False}
    cfg={'operations':[{'op':'copy_item_to_node','from_path':'$.db','source':{'match':{'version':'2025.6'}},
                        'to_path':'$.new-setting2','place':{'before_key':'shadow'}}]}
    out=YamlPatchEngine().apply_document(doc,cfg)
    assert list(out)==['version','db','new-setting2','shadow']
    assert out['new-setting2']=={'version':'2025.6','data':'A'}


def test_compiler_reuses_existing_nodes_without_indexes():
    before={'version':'1','db':[{'version':'2025.6','data':'A'},{'version':'2024.6','data':'B'}],'shadow':False}
    after={'version':'1','new-setting':deepcopy(before['db']),'shadow':False,
           'new-setting2':deepcopy(before['db'][0]),'db':deepcopy(before['db'])}
    result=DiffCompiler().compile(before,after)
    assert result.verified
    text=str(result.config)
    assert 'copy_node' in text
    assert 'copy_item_to_node' in text
    assert "'index'" not in text


def test_plan_reports_conflict_and_transaction(tmp_path: Path):
    root=tmp_path/'root'; root.mkdir()
    target=root/'FAB14-A'/'STAGING'/'app'/'application.yaml'; target.parent.mkdir(parents=True)
    dump_one({'value':0},target)
    cfg={'version':1,'options':{'conflict_policy':'warn'},'rules':[
        {'id':'a','filters':{'path_allow':['app/application.yaml']},'operations':[{'op':'set','path':'$.value','value':1}]},
        {'id':'b','filters':{'path_allow':['app/application.yaml']},'operations':[{'op':'set','path':'$.value','value':2}]},
    ]}
    plan=FolderCompiler().plan_rules_config(root,cfg)
    assert plan['summary']['conflicts']==1
    out=tmp_path/'out'
    report=FolderCompiler().apply_rules_config(root,cfg,out)
    assert load_one(out/'FAB14-A'/'STAGING'/'app'/'application.yaml')['value']==2
    assert report['plan']['conflicts']==1
