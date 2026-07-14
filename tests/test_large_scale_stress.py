from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from yaml_config_engine.engine import YamlPatchEngine
from yaml_config_engine.folder_compiler import FolderCompiler
from yaml_config_engine.yamlio import dump_one, load_one, load_all, dumps
from xml_config_engine.engine import XmlPatchEngine
from xml_config_engine.folder_compiler import XmlFolderCompiler


def _large_yaml_document(app_count: int = 12, service_count: int = 8, route_count: int = 5):
    root = CommentedMap()
    root.yaml_set_start_comment(
        'Enterprise platform configuration\n'
        'Generated stress fixture; untouched comments and positions must survive'
    )
    defaults = CommentedMap({'timeout': 30, 'retries': 3})
    defaults.yaml_set_anchor('service_defaults')
    defaults.yaml_add_eol_comment('shared defaults', 'timeout')
    root['defaults'] = defaults
    applications = CommentedMap()
    root['applications'] = applications
    for app_index in range(app_count):
        app_name = f'app-{app_index:02d}'
        app = CommentedMap()
        applications[app_name] = app
        applications.yaml_set_comment_before_after_key(
            app_name, before=f'Application {app_index:02d} - KEEP COMMENT'
        )
        app['owner'] = f'team-{app_index % 4}'
        app.yaml_add_eol_comment(f'owner-inline-{app_index:02d}', 'owner')
        services = CommentedSeq()
        app['services'] = services
        for service_index in range(service_count):
            service = CommentedMap()
            service['name'] = f'svc-{app_index:02d}-{service_index:02d}'
            service['version'] = f'{1 + service_index % 3}.0.{app_index}'
            service['enabled'] = service_index % 2 == 0
            service['config'] = CommentedMap({
                'timeout': 30 + service_index,
                'retry': CommentedMap({
                    'count': 3,
                    'delays': CommentedSeq([1, 5, 15]),
                }),
            })
            service['routes'] = CommentedSeq()
            service.yaml_add_eol_comment(
                f'service-inline-{app_index:02d}-{service_index:02d}', 'name'
            )
            for route_index in range(route_count):
                route = CommentedMap({
                    'name': f'route-{route_index}',
                    'path': f'/api/{app_index}/{service_index}/{route_index}',
                    'enabled': True,
                    'methods': CommentedSeq(['GET', 'POST']),
                })
                route.yaml_add_eol_comment(
                    f'route-inline-{app_index:02d}-{service_index:02d}-{route_index:02d}',
                    'path',
                )
                service['routes'].append(route)
            services.append(service)
    root['tail'] = CommentedMap({
        'checksum': 'keep-me',
        'notes': CommentedSeq(['alpha', 'beta']),
    })
    root.yaml_set_comment_before_after_key('tail', before='TAIL SENTINEL COMMENT')
    return root


def _independently_mutate_large_target(source):
    target = deepcopy(source)
    apps = target['applications']
    for app in apps.values():
        app['runtime'] = CommentedMap({
            'resources': CommentedMap({
                'requests': CommentedMap({'cpu': '500m', 'memory': '512Mi'}),
                'limits': CommentedMap({'cpu': '2', 'memory': '2Gi'}),
            }),
            'observability': CommentedMap({
                'metrics': True,
                'tracing': CommentedMap({'enabled': True, 'sampleRate': 0.2}),
            }),
        })
        for service in app['services']:
            service['routes'][1]['enabled'] = False
            service['metadata'] = CommentedMap({
                'managed': True,
                'tags': CommentedSeq(['enterprise', 'v2']),
            })

    services = apps['app-05']['services']
    moved = services.pop(6)
    services.insert(1, moved)
    services.pop(3)
    clone = deepcopy(services[-1])
    clone['name'] = 'svc-05-new'
    clone['version'] = '9.0.0'
    clone['config']['timeout'] = 99
    clone['config']['newSection'] = CommentedMap({
        'featureFlags': CommentedSeq([
            CommentedMap({'name': 'alpha', 'enabled': True}),
            CommentedMap({'name': 'beta', 'enabled': False}),
        ])
    })
    services.insert(2, clone)

    app_07 = apps['app-07']
    owner = app_07.pop('owner')
    app_07.insert(0, 'maintainer', owner)
    app_07.insert(1, 'deployment', CommentedMap({
        'strategy': 'canary',
        'steps': CommentedSeq([10, 30, 100]),
    }))
    target['tail']['notes'].insert(1, 'inserted')
    return target


def test_4000_line_yaml_compile_apply_is_byte_exact_and_comment_exact(tmp_path: Path):
    before = tmp_path / 'before'
    after = tmp_path / 'after'
    generated = tmp_path / 'generated'
    actual = tmp_path / 'actual'
    before.mkdir(); after.mkdir()

    source = _large_yaml_document()
    target = _independently_mutate_large_target(source)
    dump_one(source, before / 'enterprise.yaml')
    dump_one(target, after / 'enterprise.yaml')

    before_bytes = (before / 'enterprise.yaml').read_bytes()
    expected_bytes = (after / 'enterprise.yaml').read_bytes()
    assert len(before_bytes.splitlines()) > 4000
    assert before_bytes.count(b'#') > 600

    result = FolderCompiler().compile_folder(before, after, generated)
    assert result.verified
    assert sorted(p.name for p in generated.iterdir()) == ['patch.yaml']
    FolderCompiler().apply_manifest(before, generated, actual)

    actual_bytes = (actual / 'enterprise.yaml').read_bytes()
    assert actual_bytes == expected_bytes
    assert actual_bytes.count(b'#') == expected_bytes.count(b'#')
    assert b'# TAIL SENTINEL COMMENT' in actual_bytes
    assert b'# route-inline-05-07-04' in actual_bytes

    second = tmp_path / 'second'
    FolderCompiler().apply_manifest(actual, generated, second)
    assert (second / 'enterprise.yaml').read_bytes() == actual_bytes


def test_large_manual_composite_operations_keep_comments_positions_and_are_idempotent(tmp_path: Path):
    source_path = tmp_path / 'large.yaml'
    output_path = tmp_path / 'out.yaml'
    second_path = tmp_path / 'second.yaml'
    dump_one(_large_yaml_document(app_count=10, service_count=7, route_count=5), source_path)
    source_text = source_path.read_text(encoding='utf-8')
    assert len(source_text.splitlines()) > 2900

    config = {
        'version': 1,
        'options': {'atomic_write': True},
        'operations': [
            {
                'op': 'set',
                'path': '$.applications.*.runtime',
                'missing': 'create',
                'value': {
                    'resources': {
                        'requests': {'cpu': '250m', 'memory': '256Mi'},
                        'limits': {'cpu': '1', 'memory': '1Gi'},
                    },
                    'rollout': {
                        'strategy': 'canary',
                        'percentages': [10, 30, 100],
                    },
                },
            },
            {
                'op': 'set',
                'path': '$.applications.*.services[*].metadata',
                'missing': 'create',
                'value': {
                    'managed': True,
                    'labels': ['enterprise', 'stress'],
                    'policy': {'retry': 5, 'backoff': [1, 5, 15]},
                },
            },
            {
                'op': 'set',
                'path': '$.applications.*.services[*].routes[1].enabled',
                'value': False,
            },
            {
                'op': 'update_item',
                'path': '$.applications.app-05.services',
                'name_pattern': 'svc-05-*',
                'pattern_type': 'glob',
                'on_multiple_matches': 'all',
                'set': {
                    'config.retry.count': 7,
                    'config.circuitBreaker': {
                        'enabled': True,
                        'failureThreshold': 10,
                        'windowSeconds': 60,
                    },
                },
            },
            {
                'op': 'copy_item',
                'path': '$.applications.app-05.services',
                'source': {'match': {'name': 'svc-05-06'}, 'expect_matches': 1},
                'set': {'name': 'svc-05-07', 'version': '7.0.0'},
                'item_operations': [
                    {
                        'op': 'insert_key',
                        'path': '$.config',
                        'key': 'newSection',
                        'value': {
                            'flags': [
                                {'name': 'alpha', 'enabled': True},
                                {'name': 'beta', 'enabled': False},
                            ],
                            'matrix': [[1, 2], [3, 4]],
                        },
                        'position': {'last': True},
                    }
                ],
                'duplicate': {'unique_by': ['name'], 'policy': 'skip'},
                'position': {
                    'after': {'match': {'name': 'svc-05-06'}, 'expect_matches': 1}
                },
            },
            {
                'op': 'remove_item',
                'path': '$.applications.app-05.services',
                'match': {'name': 'svc-05-obsolete'},
                'missing': 'skip',
            },
            {
                'op': 'insert_key',
                'path': '$.applications.app-07',
                'key': 'deployment',
                'value': {
                    'strategy': 'blue-green',
                    'validation': {
                        'preSwitch': ['smoke', 'contract'],
                        'postSwitch': ['metrics', 'logs'],
                    },
                },
                'position': {'after_key': 'owner'},
            },
        ],
    }

    engine = YamlPatchEngine()
    engine.apply_file(source_path, config, output_path)
    output_text = output_path.read_text(encoding='utf-8')
    out = load_one(output_path)

    # copy_item intentionally clones the source service's one inline comment
    # and its five route comments; every original comment must still remain.
    assert output_text.count('#') == source_text.count('#') + 6
    for marker in (
        '# Application 05 - KEEP COMMENT',
        '# service-inline-05-06',
        '# route-inline-05-06-04',
        '# TAIL SENTINEL COMMENT',
    ):
        assert marker in output_text
    assert output_text.index('# Application 05 - KEEP COMMENT') < output_text.index('app-05:')
    assert list(out['applications']['app-07'])[:3] == ['owner', 'deployment', 'services']
    services = out['applications']['app-05']['services']
    assert [x['name'] for x in services][-2:] == ['svc-05-06', 'svc-05-07']
    assert services[-1]['config']['newSection']['matrix'] == [[1, 2], [3, 4]]
    assert all(x['routes'][1]['enabled'] is False for app in out['applications'].values() for x in app['services'])
    assert all('metadata' in x for app in out['applications'].values() for x in app['services'])

    before_second_run = output_path.read_bytes()
    engine.apply_file(output_path, config, output_path)
    assert output_path.read_bytes() == before_second_run


def test_large_yaml_bom_crlf_quotes_anchor_alias_and_flow_style_survive(tmp_path: Path):
    lines = [
        '# BOM + CRLF + anchor stress',
        'defaults: &defaults',
        '  timeout: 30  # keep timeout comment',
        '  tags: ["A", \'B\', C]',
        'services:',
    ]
    for i in range(180):
        lines.extend([
            f'  - name: "service-{i:03d}"  # service {i:03d}',
            '    <<: *defaults',
            f'    endpoint: \'/api/{i:03d}\'',
            '    enabled: true',
        ])
    lines.extend(['tail: "KEEP"  # tail-inline', ''])
    source_bytes = b'\xef\xbb\xbf' + '\r\n'.join(lines).encode('utf-8')
    source = tmp_path / 'source.yaml'; output = tmp_path / 'output.yaml'
    source.write_bytes(source_bytes)

    config = {
        'options': {'yaml_output': {'line_ending': 'preserve', 'preserve_quotes': True}},
        'operations': [
            {'op': 'set', 'path': '$.services[100].enabled', 'value': False},
            {
                'op': 'set',
                'path': '$.services[100].runtime',
                'missing': 'create',
                'value': {'limits': {'cpu': '2', 'memory': '4Gi'}, 'ports': [8080, 9090]},
            },
        ],
    }
    YamlPatchEngine().apply_file(source, config, output)
    payload = output.read_bytes()
    assert payload.startswith(b'\xef\xbb\xbf')
    assert b'\r\n' in payload and b'\n' not in payload.replace(b'\r\n', b'')
    text = payload.decode('utf-8-sig')
    assert '&defaults' in text and '*defaults' in text
    assert 'tags: ["A", \'B\', C]' in text
    assert 'endpoint: \'/api/100\'' in text
    assert '# service 100' in text and '# tail-inline' in text
    assert len(text.splitlines()) > 700


def test_large_multidocument_yaml_compile_roundtrip_preserves_comments(tmp_path: Path):
    before = tmp_path / 'before'; after = tmp_path / 'after'; generated = tmp_path / 'generated'; actual = tmp_path / 'actual'
    before.mkdir(); after.mkdir()
    docs = []
    for doc_index in range(8):
        doc = CommentedMap()
        doc.yaml_set_start_comment(f'DOCUMENT {doc_index} HEADER')
        doc['name'] = f'doc-{doc_index}'
        doc['sections'] = CommentedSeq()
        for section_index in range(25):
            section = CommentedMap({
                'name': f'section-{section_index}',
                'settings': CommentedMap({
                    'timeout': section_index + 1,
                    'flags': CommentedSeq([True, False, True]),
                    'nested': CommentedMap({'a': 1, 'b': 2, 'c': 3}),
                }),
            })
            section.yaml_add_eol_comment(f'section-inline-{doc_index}-{section_index}', 'name')
            doc['sections'].append(section)
        docs.append(doc)
    from yaml_config_engine.yamlio import dump_all
    dump_all(docs, before / 'multi.yaml')
    target_docs = deepcopy(docs)
    target_docs[3]['sections'][10]['settings']['timeout'] = 999
    target_docs[3]['sections'][10]['settings']['newSection'] = CommentedMap({
        'routes': CommentedSeq([
            CommentedMap({'name': 'primary', 'weight': 80}),
            CommentedMap({'name': 'backup', 'weight': 20}),
        ])
    })
    target_docs[6]['sections'].insert(5, CommentedMap({
        'name': 'inserted',
        'settings': CommentedMap({'timeout': 45, 'flags': CommentedSeq([False])}),
    }))
    dump_all(target_docs, after / 'multi.yaml')
    assert len((before / 'multi.yaml').read_text().splitlines()) > 1800

    result = FolderCompiler().compile_folder(before, after, generated)
    assert result.verified
    FolderCompiler().apply_manifest(before, generated, actual)
    assert (actual / 'multi.yaml').read_bytes() == (after / 'multi.yaml').read_bytes()
    assert (actual / 'multi.yaml').read_bytes().count(b'#') == (after / 'multi.yaml').read_bytes().count(b'#')


def _large_xml(component_count: int = 120, endpoint_count: int = 6) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<!-- ENTERPRISE XML HEADER -->', '<configuration xmlns:x="urn:test">', '  <components>']
    for i in range(component_count):
        parts.append(f'    <!-- COMPONENT {i:03d} KEEP -->')
        parts.append(f'    <component id="c{i:03d}" name="component-{i:03d}" enabled="true">')
        parts.append(f'      <description><![CDATA[component <{i}> & data]]></description>')
        parts.append('      <endpoints>')
        for j in range(endpoint_count):
            parts.append(f'        <endpoint name="e{j}" path="/api/{i}/{j}" method="GET"><timeout>{30+j}</timeout></endpoint>')
        parts.append('      </endpoints>')
        parts.append('      <x:metadata owner="platform" tier="gold"/>')
        parts.append('    </component>')
    parts.extend(['  </components>', '  <!-- XML TAIL KEEP -->', '  <tail checksum="keep"/>', '</configuration>', ''])
    return '\n'.join(parts)


def test_large_xml_surgical_operations_preserve_all_unmodified_text_and_positions(tmp_path: Path):
    source = _large_xml()
    assert len(source.splitlines()) > 1200
    config = {
        'version': 1,
        'format': 'xml',
        'operations': [
            {'op': 'set', 'path': "/configuration/components/component[@id='c050']/@enabled", 'value': 'false'},
            {'op': 'set', 'path': "/configuration/components/component[@id='c050']/endpoints/endpoint[2]/timeout", 'value': '99'},
            {
                'op': 'update_item',
                'path': '/configuration/components',
                'element': 'component',
                'match': {'@id': {'$pattern': 'c05*', '$pattern_type': 'glob'}},
                'on_multiple_matches': 'all',
                'set': {'@reviewed': 'yes'},
            },
            {
                'op': 'set',
                'path': "/configuration/components/component[@id='c050']/runtime",
                'missing': 'create',
                'value': {
                    'resources': {'cpu': '2', 'memory': '4Gi'},
                    'flags': {'canary': 'true', 'audit': 'true'},
                },
            },
        ],
    }
    out, _ = XmlPatchEngine().apply_text(source, config)
    ET.fromstring(out)
    assert '<!-- ENTERPRISE XML HEADER -->' in out
    assert '<!-- XML TAIL KEEP -->' in out
    assert out.count('<!-- COMPONENT ') == 120
    assert '<![CDATA[component <50> & data]]>' in out
    assert 'xmlns:x="urn:test"' in out and '<x:metadata owner="platform" tier="gold"/>' in out
    assert out.index('<!-- COMPONENT 050 KEEP -->') < out.index('<component id="c050"')
    assert 'component id="c050" name="component-050" enabled="false" reviewed="yes"' in out
    assert '<timeout>99</timeout>' in out
    assert '<runtime>' in out and '<memory>4Gi</memory>' in out
    untouched = re.search(r'    <!-- COMPONENT 049 KEEP -->.*?    </component>', source, re.S).group(0)
    assert untouched in out
    second, _ = XmlPatchEngine().apply_text(out, config)
    assert second == out


def test_many_file_folder_compile_create_delete_patch_and_unchanged(tmp_path: Path):
    before = tmp_path / 'before'; after = tmp_path / 'after'; generated = tmp_path / 'generated'; actual = tmp_path / 'actual'
    before.mkdir(); after.mkdir()
    for fab in ('FAB14', 'FAB14-FZ1'):
        for env in ('PROD', 'STAGING'):
            for app_index in range(8):
                rel = Path(fab) / env / f'app-{app_index:02d}' / 'application.yaml'
                src = _large_yaml_document(app_count=2, service_count=3, route_count=3)
                src['deployment'] = CommentedMap({'fab': fab, 'env': env, 'app': app_index})
                (before / rel).parent.mkdir(parents=True, exist_ok=True)
                (after / rel).parent.mkdir(parents=True, exist_ok=True)
                dump_one(src, before / rel)
                target = deepcopy(src)
                if app_index % 3 == 0:
                    target['deployment']['generation'] = 2
                    target['applications']['app-00']['services'][1]['config']['timeout'] = 88
                dump_one(target, after / rel)
    # One deleted file and one new deeply structured file.
    deleted = Path('FAB14') / 'PROD' / 'app-07' / 'application.yaml'
    (after / deleted).unlink()
    created = Path('FAB14-FZ1') / 'STAGING' / 'app-new' / 'application.yaml'
    created_doc = _large_yaml_document(app_count=3, service_count=4, route_count=4)
    (after / created).parent.mkdir(parents=True, exist_ok=True)
    dump_one(created_doc, after / created)

    result = FolderCompiler().compile_folder(before, after, generated, include_unchanged=True)
    assert result.verified
    assert sorted(p.name for p in generated.iterdir()) == ['patch.yaml']
    patch = load_one(generated / 'patch.yaml')
    assert patch['summary']['patch'] >= 8
    assert patch['summary']['create'] == 1
    assert patch['summary']['delete'] == 1
    assert patch['summary']['unchanged'] > 0

    FolderCompiler().apply_manifest(before, generated, actual)
    assert FolderCompiler().verify_manifest(before, generated, after)
    for expected in after.rglob('*.yaml'):
        rel = expected.relative_to(after)
        assert (actual / rel).read_bytes() == expected.read_bytes()
    assert not (actual / deleted).exists()
    assert (actual / created).exists()


def test_large_child_folder_rules_with_external_mapping_keep_unmatched_files_byte_exact(tmp_path: Path):
    source = tmp_path / 'source'
    output = tmp_path / 'output'
    second = tmp_path / 'second'
    targeted = source / 'FAB14-FZ1' / 'STAGING' / 'app-a' / 'application.yaml'
    untouched = source / 'FAB14-FZ1' / 'PROD' / 'app-b' / 'application.yaml'
    targeted.parent.mkdir(parents=True)
    untouched.parent.mkdir(parents=True)
    large = _large_yaml_document(app_count=6, service_count=6, route_count=5)
    dump_one(large, targeted)
    dump_one(deepcopy(large), untouched)
    assert len(targeted.read_text(encoding='utf-8').splitlines()) > 1400
    untouched_bytes = untouched.read_bytes()
    original_comment_count = targeted.read_bytes().count(b'#')

    variable_map = tmp_path / 'variable-map.yaml'
    variable_map.write_text(
        '''variable_map:\n'''
        '''  FAB14:\n'''
        '''    OWNER: generic-owner\n'''
        '''    TIER: generic\n'''
        '''  FAB14:STAGING:\n'''
        '''    OWNER: staging-owner\n'''
        '''    TIER: staging\n'''
        '''  FAB14-FZ1:STAGING:\n'''
        '''    OWNER: fz1-platform\n'''
        '''    TIER: gold\n''',
        encoding='utf-8',
    )
    config = tmp_path / 'rules.yaml'
    config.write_text(
        '''version: 1\n'''
        '''variable_map_file: variable-map.yaml\n'''
        '''rules:\n'''
        '''  - id: app-a-staging-large\n'''
        '''    priority: 100\n'''
        '''    filters:\n'''
        '''      path_allow: [app-a/**]\n'''
        '''    operations:\n'''
        '''      - op: set\n'''
        '''        path: $.applications.*.runtime\n'''
        '''        missing: create\n'''
        '''        value:\n'''
        '''          owner: "{{ OWNER }}"\n'''
        '''          tier: "{{ TIER }}"\n'''
        '''          rollout:\n'''
        '''            strategy: canary\n'''
        '''            percentages: [10, 30, 100]\n'''
        '''      - op: set\n'''
        '''        path: $.applications.*.services[*].routes[2].enabled\n'''
        '''        value: false\n'''
        '''      - op: update_item\n'''
        '''        path: $.applications.app-02.services\n'''
        '''        name_pattern: svc-02-*\n'''
        '''        pattern_type: glob\n'''
        '''        on_multiple_matches: all\n'''
        '''        set:\n'''
        '''          config.environmentTier: "{{ TIER }}"\n''',
        encoding='utf-8',
    )

    compiler = FolderCompiler()
    compiler.apply_rules_config(source, config, output)
    assert (output / untouched.relative_to(source)).read_bytes() == untouched_bytes
    target_out = output / targeted.relative_to(source)
    assert target_out.read_bytes().count(b'#') == original_comment_count
    parsed = load_one(target_out)
    for app in parsed['applications'].values():
        assert app['runtime']['owner'] == 'fz1-platform'
        assert app['runtime']['tier'] == 'gold'
        assert all(service['routes'][2]['enabled'] is False for service in app['services'])
    assert all(
        service['config']['environmentTier'] == 'gold'
        for service in parsed['applications']['app-02']['services']
    )

    compiler.apply_rules_config(output, config, second)
    assert (second / targeted.relative_to(source)).read_bytes() == target_out.read_bytes()
    assert (second / untouched.relative_to(source)).read_bytes() == untouched_bytes


def test_large_bom_crlf_yaml_compile_folder_is_exact_even_with_new_comment_and_section(tmp_path: Path):
    before = tmp_path / 'before'; after = tmp_path / 'after'
    generated = tmp_path / 'generated'; actual = tmp_path / 'actual'
    before.mkdir(); after.mkdir()
    lines = ['# LARGE BOM CRLF HEADER', 'services:']
    for index in range(320):
        lines.extend([
            f'  - name: "svc-{index:03d}"  # service-comment-{index:03d}',
            '    enabled: true',
            '    config:',
            f'      timeout: {30 + index % 10}',
            '      retry: {count: 3, delays: [1, 5, 15]}',
        ])
    lines.extend(['tail: "KEEP"  # tail-comment', ''])
    source_text = '\r\n'.join(lines)
    source_bytes = b'\xef\xbb\xbf' + source_text.encode('utf-8')
    (before / 'large.yaml').write_bytes(source_bytes)

    old_block = (
        '  - name: "svc-120"  # service-comment-120\r\n'
        '    enabled: true\r\n'
        '    config:\r\n'
        '      timeout: 30\r\n'
        '      retry: {count: 3, delays: [1, 5, 15]}\r\n'
    )
    new_block = (
        '  - name: "svc-120"  # service-comment-120\r\n'
        '    enabled: false\r\n'
        '    config:\r\n'
        '      timeout: 99\r\n'
        '      retry: {count: 3, delays: [1, 5, 15]}\r\n'
        '    # NEW RUNTIME COMMENT MUST BE EXACT\r\n'
        '    runtime:\r\n'
        '      resources:\r\n'
        '        cpu: "2"\r\n'
        '        memory: 4Gi\r\n'
        '      ports: [8080, 9090]\r\n'
    )
    assert old_block in source_text
    target_text = source_text.replace(old_block, new_block, 1)
    target_bytes = b'\xef\xbb\xbf' + target_text.encode('utf-8')
    (after / 'large.yaml').write_bytes(target_bytes)
    assert len(target_text.splitlines()) > 1600

    result = FolderCompiler().compile_folder(before, after, generated)
    assert result.verified
    patch = load_one(generated / 'patch.yaml')
    entry = patch['files']['large.yaml']
    assert entry['strategy'] in {'structural-operations-exact', 'replace-entire-file-exact'}
    FolderCompiler().apply_manifest(before, generated, actual)
    actual_bytes = (actual / 'large.yaml').read_bytes()
    assert actual_bytes == target_bytes
    assert actual_bytes.startswith(b'\xef\xbb\xbf')
    assert b'\r\n' in actual_bytes and b'\n' not in actual_bytes.replace(b'\r\n', b'')
    assert b'# NEW RUNTIME COMMENT MUST BE EXACT' in actual_bytes

    before_second = actual_bytes
    FolderCompiler().apply_manifest(actual, generated, tmp_path / 'second-exact')
    assert (tmp_path / 'second-exact' / 'large.yaml').read_bytes() == before_second


def test_large_xml_compile_folder_preserves_bom_crlf_comments_create_delete_and_exact_position(tmp_path: Path):
    before = tmp_path / 'before'; after = tmp_path / 'after'
    generated = tmp_path / 'generated'; actual = tmp_path / 'actual'
    before.mkdir(); after.mkdir()
    source_text = _large_xml(component_count=140, endpoint_count=7).replace('\n', '\r\n')
    source_bytes = b'\xef\xbb\xbf' + source_text.encode('utf-8')
    (before / 'enterprise.xml').write_bytes(source_bytes)
    (before / 'obsolete.xml').write_bytes(b'\xef\xbb\xbf<obsolete/>\r\n')

    marker = (
        '      <x:metadata owner="platform" tier="gold"/>\r\n'
        '    </component>\r\n'
        '    <!-- COMPONENT 071 KEEP -->'
    )
    replacement = (
        '      <x:metadata owner="platform" tier="platinum"/>\r\n'
        '      <!-- RUNTIME 070 EXACT POSITION -->\r\n'
        '      <runtime mode="canary"><cpu>2</cpu><memory>4Gi</memory></runtime>\r\n'
        '    </component>\r\n'
        '    <!-- COMPONENT 071 KEEP -->'
    )
    # Replace the boundary immediately before component 071, which belongs to component 070.
    assert marker in source_text
    target_text = source_text.replace(marker, replacement, 1)
    target_text = target_text.replace('component id="c070" name="component-070" enabled="true"',
                                      'component id="c070" name="component-070" enabled="false"', 1)
    target_bytes = b'\xef\xbb\xbf' + target_text.encode('utf-8')
    (after / 'enterprise.xml').write_bytes(target_bytes)
    new_bytes = b'\xef\xbb\xbf<?xml version="1.0"?>\r\n<!-- NEW FILE -->\r\n<root><value>1</value></root>\r\n'
    (after / 'new.xml').write_bytes(new_bytes)
    assert len(target_text.splitlines()) > 1500

    result = XmlFolderCompiler().compile_folder(before, after, generated)
    assert result.verified
    assert sorted(path.name for path in generated.iterdir()) == ['patch.yaml']
    XmlFolderCompiler().apply_folder(before, generated, actual)
    assert (actual / 'enterprise.xml').read_bytes() == target_bytes
    assert (actual / 'new.xml').read_bytes() == new_bytes
    assert not (actual / 'obsolete.xml').exists()
    enterprise = (actual / 'enterprise.xml').read_bytes()
    assert enterprise.startswith(b'\xef\xbb\xbf')
    assert b'\r\n' in enterprise and b'\n' not in enterprise.replace(b'\r\n', b'')
    assert enterprise.count(b'<!-- COMPONENT ') == 140
    assert enterprise.index(b'<!-- RUNTIME 070 EXACT POSITION -->') < enterprise.index(b'<!-- COMPONENT 071 KEEP -->')
