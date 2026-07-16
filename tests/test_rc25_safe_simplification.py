from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.yaml_config_engine.diff_compiler import DiffCompiler


def main() -> None:
    compiler = DiffCompiler()

    before = {'items': [{'name': 'A', 'x': 0, 'y': 0}]}
    after = {'items': [{'name': 'A', 'x': 1, 'y': 2}]}
    operations = [
        {'op': 'update_item', 'path': '$/items', 'match': {'name': 'A'}, 'set': {'x': 1}},
        {'op': 'update_item', 'path': '$/items', 'match': {'name': 'A'}, 'set': {'y': 2}},
    ]
    merged = compiler._optimize_same_target_updates(before, after, operations)
    assert len(merged) == 1
    assert merged[0]['set'] == {'x': 1, 'y': 2}

    before = {'apps': {'a': {'x': 0}, 'b': {'x': 0}}}
    after = {'apps': {'a': {'x': 1}, 'b': {'x': 1}}}
    operations = [
        {'op': 'replace', 'paths': ['$/apps/*/x', '$/apps/a/x'], 'value': 1}
    ]
    reduced = compiler._remove_redundant_paths(before, after, operations)
    assert reduced == [{'op': 'replace', 'path': '$/apps/*/x', 'value': 1}]

    print('PASS: rc25 safe simplification')


if __name__ == '__main__':
    main()
