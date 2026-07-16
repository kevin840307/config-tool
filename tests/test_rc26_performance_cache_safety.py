from copy import deepcopy
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from src.yaml_config_engine.template import render_value, _compiled_expression, _compiled_template
from src.yaml_config_engine.pathing import parse_path, expand_paths, _parse_path_cached
from src.yaml_config_engine.engine import YamlPatchEngine
from src.yaml_config_engine.comparison import strict_equal


def require(v,msg):
    if not v: raise AssertionError(msg)


def main():
    # Literal strings must bypass Jinja without changing special YAML-like text.
    literals=['plain','a:b','{not-template}','${ENV}','100%','x [*] y']
    for value in literals:
        require(render_value(value, {'x':1}) == value, f'literal changed: {value!r}')
    try:
        render_value('a {{broken', {'x':1})
    except Exception:
        pass
    else:
        raise AssertionError('malformed template must still fail')

    # Same compiled template must render independently for different contexts.
    _compiled_expression.cache_clear(); _compiled_template.cache_clear()
    require(render_value('{{ version }}', {'version':'v1'}) == 'v1', 'expression v1')
    require(render_value('{{ version }}', {'version':'v2'}) == 'v2', 'expression cache leaked context')
    require(render_value('app-{{ version }}', {'version':'v3'}) == 'app-v3', 'template v3')
    require(render_value('app-{{ version }}', {'version':'v4'}) == 'app-v4', 'template cache leaked context')
    require(_compiled_expression.cache_info().hits >= 1, 'expression cache was not used')
    require(_compiled_template.cache_info().hits >= 1, 'template cache was not used')

    # parse_path preserves public mutable-list behavior while internal result is cached.
    _parse_path_cached.cache_clear()
    first=parse_path('$/apps/[a,b]/versions/0')
    second=parse_path('$/apps/[a,b]/versions/0')
    require(first == second and first is not second, 'parse_path must return equal fresh lists')
    first.append('mutated')
    require(parse_path('$/apps/[a,b]/versions/0') == second, 'caller mutation polluted path cache')
    require(_parse_path_cached.cache_info().hits >= 2, 'path cache was not used')
    root={'apps':{'a':{'versions':[1]},'b':{'versions':[2]}}}
    require(expand_paths(root,'$/apps/[a,b]/versions/0') == ['$/apps/a/versions/0','$/apps/b/versions/0'], 'union expansion changed')

    # Original snapshot semantics must remain exact when referenced.
    before={'value':'old','copy':'none'}
    cfg={'version':1,'operations':[
        {'op':'replace','path':'$/value','value':'new'},
        {'op':'replace','path':'$/copy','value':'{{ original.value }}'},
    ]}
    actual=YamlPatchEngine().apply_document(deepcopy(before),cfg,track_no_effect=False)
    require(strict_equal(actual,{'value':'new','copy':'old'}),'original snapshot semantics changed')

    print('PASS: rc26 performance cache safety')

if __name__=='__main__': main()
