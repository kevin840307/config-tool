from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from mixed_folder import MixedFolderCompiler
from yaml_config_engine.yamlio import load_one

def plain(v):
    if isinstance(v,dict): return {str(k):plain(x) for k,x in v.items()}
    if isinstance(v,list): return [plain(x) for x in v]
    if isinstance(v,str): return str(v)
    return v

def main():
    import tempfile, shutil
    fx=ROOT/'examples'/'same-fab-phase-version-real'
    with tempfile.TemporaryDirectory(prefix='same-fab-version-') as t:
      generated=Path(t)/'generated'
      compiler=MixedFolderCompiler()
      compiler.compile_folder(fx/'source-before'/'F13'/'STG',fx/'source-after'/'F13'/'STG',generated,variable_map_files=[fx/'source-variable-map.yaml'])
      patch_text=(generated/'patch.yaml').read_text()
      assert patch_text.count("path: $/apps/appA/phases/p2/versions") == 1  # copy only
      assert patch_text.count("path: $/apps/appA/phases/f13p1/versions") == 1  # copy only
      assert "paths:\n            - $/apps/appA/phases/p2/versions\n            - $/apps/appA/phases/f13p1/versions" in patch_text or "path: $/apps/appA/phases/*/versions" in patch_text
      out=Path(t)/'out'
      MixedFolderCompiler().apply_folder(fx/'target-before'/'F13'/'STG',generated/'patch.yaml',out,variable_map_files=[fx/'target-variable-map.yaml'])
      expected=fx/'target-expected'/'F13'/'STG'
      af={p.relative_to(out) for p in out.rglob('*') if p.is_file()}; bf={p.relative_to(expected) for p in expected.rglob('*') if p.is_file()}
      assert af==bf
      for rel in af:
        if rel.suffix.lower() in ('.yaml','.yml'):
          assert plain(load_one(out/rel))==plain(load_one(expected/rel)), rel
        else:
          assert (out/rel).read_text(encoding='utf-8-sig')==(expected/rel).read_text(encoding='utf-8-sig'), rel
      txt=(generated/'patch.yaml').read_text()
      assert '{{ old_version }}' in txt and '{{ current_version }}' in txt and '{{ new_version }}' in txt
      assert 'fab14' not in txt and 'f14' not in txt
      assert '*' in txt or 'paths:' in txt
      assert len((fx/'source-before'/'F13'/'STG'/'values.yaml').read_text().splitlines())>=2500
    print('PASS: same FAB phase/version folder regression')
if __name__=='__main__': main()
