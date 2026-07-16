from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from mixed_folder import MixedFolderCompiler
from yaml_config_engine.yamlio import load_one

HERE=Path(__file__).resolve().parent
out=HERE/'rerun-applied'
if out.exists():
 import shutil; shutil.rmtree(out)
MixedFolderCompiler().apply_folder(HERE/'target-before'/'F13'/'STG',HERE/'generated'/'patch.yaml',out/'F13'/'STG',variable_map_files=[HERE/'target-variable-map.yaml'])
print('APPLY PASS:',out)
