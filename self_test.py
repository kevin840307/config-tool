from __future__ import annotations
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess, sys, textwrap, os, time
from config_tool_api import ConfigTool
from yaml_config_engine.yamlio import load_one
from yaml_config_engine.diff_compiler import DiffCompiler


def write(p: Path, s: str):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(textwrap.dedent(s).lstrip(), encoding='utf-8')

def check(name, cond):
    if not cond: raise AssertionError(name)
    print(f'PASS: {name}')

def main():
    t=ConfigTool()
    with TemporaryDirectory(prefix='config-tool-selftest-') as td:
        root=Path(td)
        # 1 basic nested output + compile/apply/verify
        b=root/'basic/before.yaml'; a=root/'basic/after.yaml'; p=root/'out/nested/patch.yaml'; o=root/'out/deep/result.yaml'
        write(b, 'app:\n  version: 1\n  enabled: false\n')
        write(a, 'app:\n  version: 2\n  enabled: true\n')
        r=t.compile(b,a,p); check('yaml compile', r.ok and p.exists())
        t.apply(b,p,o); check('nested output directory', o.exists())
        check('yaml verify', t.verify(b,p,a).verified)

        # 2 single mapping path must not be iterated as characters
        m=root/'map.yaml'; write(m, "variable_map:\n  global:\n    VERSION: '2'\n")
        bm=root/'map_before.yaml'; am=root/'map_after.yaml'; pm=root/'map_patch.yaml'
        write(bm, "version: '1'\n"); write(am, "version: '2'\n")
        rr=t.compile(bm,am,pm,variable_map_files=m)
        check('single mapping path', rr.ok and rr.data.get('generalized'))
        check('mapped verify', t.verify(bm,pm,am).verified)

        # 3 quote-only changes
        qb=root/'q_before.yaml'; qa=root/'q_after.yaml'; qp=root/'q_patch.yaml'
        write(qb, 'p: 2026.04.0\ns: \'2026.04.0\'\nd: "2026.04.0"\n')
        write(qa, 'p: \'2026.04.0\'\ns: "2026.04.0"\nd: 2026.04.0\n')
        t.compile(qb,qa,qp); check('quote style verify', t.verify(qb,qp,qa).verified)

        # 4 full sibling wildcard
        wb=root/'w_before.yaml'; wa=root/'w_after.yaml'; wp=root/'w_patch.yaml'
        write(wb, 'fab:\n  p1: {enabled: false}\n  p2: {enabled: false}\n  p3: {enabled: false}\n')
        write(wa, 'fab:\n  p1: {enabled: true}\n  p2: {enabled: true}\n  p3: {enabled: true}\n')
        t.compile(wb,wa,wp); txt=wp.read_text(); check('full sibling wildcard', '/fab/*/enabled' in txt)
        check('wildcard replay', t.verify(wb,wp,wa).verified)

        # 5 all list items [*]
        lb=root/'l_before.yaml'; la=root/'l_after.yaml'; lp=root/'l_patch.yaml'
        write(lb, 'services:\n  - {name: a, enabled: false}\n  - {name: b, enabled: false}\n  - {name: c, enabled: false}\n')
        write(la, 'services:\n  - {name: a, enabled: true}\n  - {name: b, enabled: true}\n  - {name: c, enabled: true}\n')
        t.compile(lb,la,lp); check('all-list selector', '[*]' in lp.read_text())
        check('all-list replay', t.verify(lb,lp,la).verified)

        # 6 missing defaults skip
        src=root/'skip.yaml'; cfg=root/'skip_patch.yaml'; out=root/'skip_out.yaml'
        write(src, 'a: 1\n'); write(cfg, 'version: 1\noperations:\n  - op: replace\n    path: $/missing/value\n    value: 2\n')
        ar=t.apply(src,cfg,out); check('missing default skip', ar.ok and ar.skipped_operations)

        # 7 multi-document
        mb=root/'multi_before.yaml'; ma=root/'multi_after.yaml'; mp=root/'multi_patch.yaml'
        write(mb, 'a: 1\n---\nb: 2\n'); write(ma, 'a: 3\n---\nb: 4\n')
        t.compile(mb,ma,mp); check('multi-document replay', t.verify(mb,mp,ma).verified)

        # 8 xml basic
        xb=root/'before.xml'; xa=root/'after.xml'; xp=root/'xml_patch.yaml'
        write(xb, '<root><app enabled="false">1</app></root>\n'); write(xa, '<root><app enabled="true">2</app></root>\n')
        t.compile(xb,xa,xp); check('xml replay', t.verify(xb,xp,xa).verified)

        # 9 mixed folder
        bf=root/'folders/before'; af=root/'folders/after'; gf=root/'folders/generated'; of=root/'folders/output'
        write(bf/'a.yaml','v: 1\n'); write(af/'a.yaml','v: 2\n'); write(bf/'b.xml','<r><v>1</v></r>\n'); write(af/'b.xml','<r><v>2</v></r>\n')
        cr=t.compile_folder(bf,af,gf); check('mixed compile folder', cr.ok)
        t.apply_folder(bf,gf,of); check('mixed verify folder', t.verify_folder(bf,gf,af).verified)

        # 10 folder create/delete/compact/expanded/matched-only and mapping parity
        fb=root/'folder_matrix/before'; fa=root/'folder_matrix/after'
        write(fb/'FAB14/STAGING/keep.yaml', "host: old\n")
        write(fa/'FAB14/STAGING/keep.yaml', "host: new\n")
        write(fb/'delete.yaml', "obsolete: true\n")
        write(fa/'create.yaml', "created: true\n")
        write(fb/'xkeep.xml', '<root><v>1</v></root>\n')
        write(fa/'xkeep.xml', '<root><v>2</v></root>\n')
        write(fb/'xdelete.xml', '<root/>\n')
        write(fa/'xcreate.xml', '<created value="yes"/>\n')

        for layout in ('compact','expanded'):
            gen=root/f'folder_matrix/gen_{layout}'; outm=root/f'folder_matrix/out_{layout}'
            rr=t.compile_folder(fb,fa,gen,layout=layout)
            check(f'mixed {layout} compile', rr.ok and rr.verified)
            t.apply_folder(fb,gen,outm)
            check(f'mixed {layout} create/delete replay', t.verify_folder(fb,gen,fa).verified)
            if layout == 'compact':
                compact=(gen/'patch.yaml').read_text(encoding='utf-8')
                check('compact readable XML create', 'create_text:' in compact and 'create_bytes_base64:' not in compact)

        # YAML-only Python API must pass runtime mapping through compact and expanded layouts.
        mapfile=root/'folder_matrix/runtime-map.yaml'
        write(mapfile, "variable_map:\n  global:\n    HOST: mapped-host\n")
        ysrc=root/'folder_matrix/yaml_source'; write(ysrc/'FAB14/STAGING/app.yaml', 'host: old\n')
        for layout in ('compact','expanded'):
            ygen=root/f'folder_matrix/ygen_{layout}'; yout=root/f'folder_matrix/yout_{layout}'
            # Hand-authored folder patch config uses runtime mapping supplied through facade.
            if layout == 'compact':
                write(ygen/'patch.yaml', """
                version: 1
                kind: yaml-folder-patch-compact
                files:
                  FAB14/STAGING/app.yaml:
                    config:
                      version: 1
                      operations:
                        - op: replace
                          path: $/host
                          value: '{{ HOST }}'
                """)
            else:
                write(ygen/'configs/FAB14/STAGING/app.yaml.config.yaml', """
                version: 1
                operations:
                  - op: replace
                    path: $/host
                    value: '{{ HOST }}'
                """)
                write(ygen/'manifest.yaml', """
                version: 1
                kind: yaml-folder-manifest
                files:
                  - relative_path: FAB14/STAGING/app.yaml
                    action: patch
                    config: configs/FAB14/STAGING/app.yaml.config.yaml
                """)
            t.apply_folder(ysrc,ygen,yout,format='yaml',variable_map_files=mapfile)
            check(f'yaml {layout} mapping parity', 'mapped-host' in (yout/'FAB14/STAGING/app.yaml').read_text())

        # XML-only compact should remain readable and expanded must pass mapping files.
        xbefore=root/'folder_matrix/xml_before'; xafter=root/'folder_matrix/xml_after'
        write(xafter/'new.xml','<new><v>1</v></new>\n')
        xgen=root/'folder_matrix/xml_gen'; t.compile_folder(xbefore,xafter,xgen,format='xml',layout='compact')
        xtxt=(xgen/'patch.yaml').read_text(encoding='utf-8')
        check('xml-only compact no base64 for UTF-8', 'create_text:' in xtxt and 'create_bytes_base64:' not in xtxt)

        # matched-files-only leaves create/delete files untouched and only patches common files.
        mgen=root/'folder_matrix/matched'; mout=root/'folder_matrix/matched_out'
        t.compile_folder(fb,fa,mgen,matched_files_only=True)
        t.apply_folder(fb,mgen,mout)
        check('matched-only keeps source-only file', (mout/'delete.yaml').exists())
        check('matched-only ignores after-only file', not (mout/'create.yaml').exists())
        check('matched-only patches common file', 'new' in (mout/'FAB14/STAGING/keep.yaml').read_text())


        # 12 legacy patch compatibility: full v0.8-style and readable v0.9-style aliases
        legacy_src=root/'legacy/source.yaml'
        write(legacy_src, """
        items:
          - name: old
            enabled: false
        """)
        legacy_full=root/'legacy/full.yaml'; legacy_full_out=root/'legacy/full_out.yaml'
        write(legacy_full, """
        version: 1
        options: {atomic_write: true}
        operations:
          - op: update_item
            path: $/items
            match: {name: old}
            item_operations:
              - op: replace
                path: $/enabled
                value: true
            expect_matches: 1
        """)
        t.apply(legacy_src, legacy_full, legacy_full_out)
        check('legacy full patch compatibility', 'enabled: true' in legacy_full_out.read_text())
        legacy_readable=root/'legacy/readable.yaml'; legacy_readable_out=root/'legacy/readable_out.yaml'
        write(legacy_readable, """
        version: 1
        operations:
          - op: copy_item
            path: $/items
            from: {name: old}
            set: {name: new}
            before: {name: old}
        """)
        t.apply(legacy_src, legacy_readable, legacy_readable_out)
        check('readable patch compatibility', 'name: new' in legacy_readable_out.read_text())

        # 13 atomic/recovery: failed rendering must not overwrite an existing output
        atomic_src=root/'atomic/source.yaml'; atomic_cfg=root/'atomic/bad.yaml'; atomic_out=root/'atomic/output.yaml'
        write(atomic_src, 'value: old\n'); write(atomic_out, 'sentinel: keep\n')
        write(atomic_cfg, """
        version: 1
        operations:
          - op: replace
            path: $/value
            value: '{{ MISSING_VARIABLE }}'
        """)
        try:
            t.apply(atomic_src, atomic_cfg, atomic_out)
        except Exception as e:
            check('missing variable error is explicit', 'MISSING_VARIABLE' in str(e))
        else:
            raise AssertionError('missing variable error is explicit')
        check('atomic failure preserves existing output', atomic_out.read_text() == 'sentinel: keep\n')

        # 14 verify_folder accepts the same runtime mapping contract as apply_folder
        verify_map=root/'verify-map.yaml'
        write(verify_map, """
        variable_map:
          global:
            HOST: verified-host
        """)
        vsrc=root/'verify-folder/source'; vgen=root/'verify-folder/generated'; vexp=root/'verify-folder/expected'
        write(vsrc/'app.yaml', 'host: old\n'); write(vexp/'app.yaml', 'host: verified-host\n')
        write(vgen/'patch.yaml', """
        version: 1
        kind: yaml-folder-patch-compact
        files:
          app.yaml:
            config:
              version: 1
              operations:
                - op: replace
                  path: $/host
                  value: '{{ HOST }}'
        """)
        check('verify-folder runtime mapping parity', t.verify_folder(vsrc,vgen,vexp,format='yaml',variable_map_files=verify_map).verified)

        # 15 compact folder file-key templates and wildcard matching
        fsrc=root/'file-key/source'; fgen=root/'file-key/generated'; fout=root/'file-key/output'
        write(fsrc/'v1/application.yaml', 'version: old\n')
        write(fsrc/'v2/application.yaml', 'version: old\n')
        write(fsrc/'nested/x/application.yaml', 'version: old\n')
        write(fgen/'patch.yaml', """
        version: 1
        kind: yaml-folder-patch-compact
        files:
          "*/application.yaml":
            ops:
              - set: [$/version, wildcard]
          "**/application.yaml":
            ops:
              - set: [$/version, recursive]
        """)
        try:
            t.apply_folder(fsrc,fgen,fout,format='yaml')
        except ValueError as e:
            check('file wildcard overlap is explicit', 'overlap' in str(e))
        else:
            raise AssertionError('file wildcard overlap is explicit')

        write(fgen/'patch.yaml', """
        version: 1
        kind: yaml-folder-patch-compact
        files:
          "*/application.yaml":
            ops:
              - set: [$/version, wildcard]
          "{{ TARGET }}/created.yaml":
            create_documents:
              - created: true
        """)
        t.apply_folder(fsrc,fgen,fout,format='yaml',variables={'TARGET':'v3'})
        check('file wildcard matches one directory level',
              'wildcard' in (fout/'v1/application.yaml').read_text() and
              'wildcard' in (fout/'v2/application.yaml').read_text() and
              'old' in (fout/'nested/x/application.yaml').read_text())
        check('file key variable creates concrete path', (fout/'v3/created.yaml').exists())

        write(fgen/'patch.yaml', """
        version: 1
        kind: yaml-folder-patch-compact
        files:
          "**/application.yaml":
            ops:
              - set: [$/version, recursive]
        """)
        t.apply_folder(fsrc,fgen,fout,format='yaml')
        check('recursive file wildcard matches nested files',
              all('recursive' in (fout/x).read_text() for x in
                  ('v1/application.yaml','v2/application.yaml','nested/x/application.yaml')))

        write(fgen/'patch.yaml', """
        version: 1
        kind: yaml-folder-patch-compact
        files:
          "../escape.yaml":
            create_documents:
              - unsafe: true
        """)
        try:
            t.apply_folder(fsrc,fgen,fout,format='yaml')
        except ValueError as e:
            check('file key path traversal rejected', 'Unsafe' in str(e))
        else:
            raise AssertionError('file key path traversal rejected')

        # 16 auto compile-folder file-key generalization
        auto_before=root/'auto-file-key/before'; auto_after=root/'auto-file-key/after'; auto_gen=root/'auto-file-key/generated'; auto_out=root/'auto-file-key/output'
        for fab in ('FAB14','FAB15'):
            write(auto_before/fab/'application.yaml', 'enabled: false\n')
            write(auto_after/fab/'application.yaml', 'enabled: true\n')
        auto_result=t.compile_folder(auto_before,auto_after,auto_gen,format='yaml')
        auto_patch=load_one(auto_gen/'patch.yaml')
        check('auto file wildcard generated', '*/application.yaml' in auto_patch.get('files', {}))
        t.apply_folder(auto_before,auto_gen,auto_out,format='yaml')
        check('auto file wildcard replay', t.verify_folder(auto_before,auto_gen,auto_after,format='yaml').verified)

        var_before=root/'auto-file-var/before'; var_after=root/'auto-file-var/after'; var_gen=root/'auto-file-var/generated'
        write(var_before/'v512/application.yaml', 'enabled: false\n')
        write(var_after/'v512/application.yaml', 'enabled: true\n')
        t.compile_folder(var_before,var_after,var_gen,format='yaml',variables={'version':'v512'})
        var_patch=load_one(var_gen/'patch.yaml')
        check('auto file variable generated', '{{ version }}/application.yaml' in var_patch.get('files', {}))
        check('auto patch omits embedded variables', 'variables' not in var_patch and 'variable_map' not in var_patch)
        check('auto file variable replay', t.verify_folder(var_before,var_gen,var_after,format='yaml',variables={'version':'v512'}).verified)

        # 16b optimizer regression audit: wildcard-first, residuals, defaults
        yopt_before=root/'yaml-opt-before.yaml'; yopt_after=root/'yaml-opt-after.yaml'; yopt_patch=root/'yaml-opt-patch.yaml'
        write(yopt_before, '''
        abc:
          p1: "false"
          p2: "false"
          p3: "keep"
        ''')
        write(yopt_after, '''
        abc:
          p1: "true"
          p2: "true"
          p3: "keep"
        ''')
        t.compile(yopt_before,yopt_after,yopt_patch,format='yaml')
        yopt_ops=load_one(yopt_patch).get('operations', [])
        check('yaml wildcard replay authority preferred', len(yopt_ops)==1 and yopt_ops[0].get('path')=='$/abc/*')

        common_before=root/'yaml-common-before.yaml'; common_after=root/'yaml-common-after.yaml'; common_patch=root/'yaml-common-patch.yaml'
        write(common_before, '''
        abc:
          p1: {x: 0, y: 0}
          p2: {x: 0, y: 0}
        ''')
        write(common_after, '''
        abc:
          p1: {x: 1, y: 2}
          p2: {x: 1, y: 3}
        ''')
        t.compile(common_before,common_after,common_patch,format='yaml')
        common_ops=load_one(common_patch).get('operations', [])
        check('yaml common operation extracted with residuals',
              any(op.get('path')=='$/abc/*/x' for op in common_ops) and
              any(op.get('path')=='$/abc/p1/y' for op in common_ops) and
              any(op.get('path')=='$/abc/p2/y' for op in common_ops))

        update_before=root/'yaml-update-before.yaml'; update_after=root/'yaml-update-after.yaml'; update_patch=root/'yaml-update-patch.yaml'
        write(update_before, '''
        db:
          - {name: A, x: 0, y: 0}
          - {name: B, x: 0, y: 0}
          - {name: C, x: 0, y: 0}
        ''')
        write(update_after, '''
        db:
          - {name: A, x: 1, y: 2}
          - {name: B, x: 1, y: 3}
          - {name: C, x: 0, y: 0}
        ''')
        t.compile(update_before,update_after,update_patch,format='yaml')
        update_ops=load_one(update_patch).get('operations', [])
        common_update=next((op for op in update_ops if op.get('op')=='update_item' and isinstance(op.get('match'),dict) and op.get('match',{}).get('any')), None)
        check('update_item common nested operation extracted',
              common_update is not None and common_update.get('set',{}).get('x')==1 and
              any(op.get('match',{}).get('name')=='A' and op.get('set',{}).get('y')==2 for op in update_ops) and
              any(op.get('match',{}).get('name')=='B' and op.get('set',{}).get('y')==3 for op in update_ops))

        all_items_before=root/'yaml-all-items-before.yaml'; all_items_after=root/'yaml-all-items-after.yaml'; all_items_patch=root/'yaml-all-items-patch.yaml'
        write(all_items_before, '''
        app:
          p1:
            - {name: A, enabled: false}
            - {name: B, enabled: false}
          p2:
            - {name: A, enabled: false}
            - {name: B, enabled: false}
        ''')
        write(all_items_after, '''
        app:
          p1:
            - {name: A, enabled: true}
            - {name: B, enabled: true}
          p2:
            - {name: A, enabled: true}
            - {name: B, enabled: true}
        ''')
        t.compile(all_items_before,all_items_after,all_items_patch,format='yaml')
        all_items_ops=load_one(all_items_patch).get('operations', [])
        check('all-item update removes redundant match',
              all_items_ops == [{'op':'replace','path':'$/app/*/[*]/enabled','value':True}])

        partial_items_before=root/'yaml-partial-items-before.yaml'; partial_items_after=root/'yaml-partial-items-after.yaml'; partial_items_patch=root/'yaml-partial-items-patch.yaml'
        write(partial_items_before, '''
        db:
          - {name: A, x: 0}
          - {name: B, x: 0}
          - {name: C, x: 0}
        ''')
        write(partial_items_after, '''
        db:
          - {name: A, x: 1}
          - {name: B, x: 1}
          - {name: C, x: 0}
        ''')
        t.compile(partial_items_before,partial_items_after,partial_items_patch,format='yaml')
        partial_items_ops=load_one(partial_items_patch).get('operations', [])
        check('partial-item update keeps exact match',
              len(partial_items_ops)==1 and partial_items_ops[0].get('op')=='update_item' and
              partial_items_ops[0].get('match',{}).get('any') and partial_items_ops[0].get('set',{}).get('x')==1)

        normalized = DiffCompiler()._optimize_selectors(
            {'abc': {'p1': 0, 'p2': 0}},
            {'abc': {'p1': 1, 'p2': 1}},
            [
                {'op':'replace','path':'$/abc/p1','value':1,'missing':'skip'},
                {'op':'replace','path':'$/abc/p2','value':1},
            ],
        )
        check('default-equivalent operations merge', len(normalized)==1 and normalized[0].get('path')=='$/abc/*')

        collective_before=root/'yaml-collective-before.yaml'; collective_after=root/'yaml-collective-after.yaml'; collective_patch=root/'yaml-collective-patch.yaml'
        write(collective_before, '''
        a:
          b:
            c:
              p1:
                - {name: A, x: 0}
                - {name: B, x: 0}
              p2:
                - {name: A, x: 0}
                - {name: B, x: 0}
        ''')
        write(collective_after, '''
        a:
          b:
            c:
              p1:
                - {name: A, x: 1}
                - {name: B, x: 1}
              p2:
                - {name: A, x: 1}
                - {name: B, x: 1}
        ''')
        t.compile(collective_before,collective_after,collective_patch,format='yaml')
        collective_ops=load_one(collective_patch).get('operations', [])
        check('collective all-item matches removed before outer path merge',
              collective_ops == [{'op':'replace','path':'$/a/b/c/*/[*]/x','value':1}])

        update_path_normalized = DiffCompiler()._optimize_selectors(
            {'a': {'b': {'c': {
                'p1': [{'name':'A','x':0},{'name':'B','x':0}],
                'p2': [{'name':'A','x':0},{'name':'B','x':0}],
            }}}},
            {'a': {'b': {'c': {
                'p1': [{'name':'A','x':1},{'name':'B','x':0}],
                'p2': [{'name':'A','x':1},{'name':'B','x':0}],
            }}}},
            [
                {
                    'op':'update_item', 'path':'$/a/b/c/p1',
                    'match':{'name':'A'},
                    'item_operations':[{'op':'replace','path':'$/x','value':1,'missing':'skip'}],
                    'expect_matches':1,
                },
                {
                    'path':'$/a/b/c/p2', 'op':'update_item',
                    'item_operations':[{'value':1,'path':'$/x','op':'replace'}],
                    'match':{'name':'A'},
                },
            ],
        )
        check('update_item semantic-equivalent outer paths merge',
              len(update_path_normalized)==1 and
              update_path_normalized[0].get('op')=='update_item' and
              update_path_normalized[0].get('path')=='$/a/b/c/*')

        # 17a XML full-sibling wildcard remains preferred over exact union
        xml_star_before=root/'xml-star-before.xml'; xml_star_after=root/'xml-star-after.xml'; xml_star_patch=root/'xml-star-patch.yaml'; xml_star_out=root/'xml-star-out.xml'
        write(xml_star_before, '<root>\n  <abc>\n    <p1>false</p1>\n    <p2>false</p2>\n  </abc>\n</root>')
        write(xml_star_after, '<root>\n  <abc>\n    <p1>true</p1>\n    <p2>true</p2>\n  </abc>\n</root>')
        t.compile(xml_star_before,xml_star_after,xml_star_patch,format='xml')
        xml_star_config=load_one(xml_star_patch); xml_star_ops=xml_star_config.get('operations', [])
        check('xml full sibling wildcard preferred', len(xml_star_ops)==1 and xml_star_ops[0].get('path')=='/root/abc/*')
        t.apply(xml_star_before,xml_star_patch,xml_star_out,format='xml')
        check('xml full sibling wildcard replay', xml_star_out.read_text()==xml_star_after.read_text())

        # 17 single-file XML operation path generalization
        xml_union_before=root/'xml-union-before.xml'; xml_union_after=root/'xml-union-after.xml'; xml_union_patch=root/'xml-union-patch.yaml'; xml_union_out=root/'xml-union-out.xml'
        write(xml_union_before, '<root>\n  <abc>\n    <p1>false</p1>\n    <p2>false</p2>\n    <p3>false</p3>\n  </abc>\n</root>')
        write(xml_union_after, '<root>\n  <abc>\n    <p1>true</p1>\n    <p2>true</p2>\n    <p3>false</p3>\n  </abc>\n</root>')
        xml_union_result=t.compile(xml_union_before,xml_union_after,xml_union_patch,format='xml')
        xml_union_config=load_one(xml_union_patch)
        xml_union_ops=xml_union_config.get('operations', [])
        check('xml partial sibling paths generated', len(xml_union_ops)==1 and xml_union_ops[0].get('paths')==['/root/abc/p1','/root/abc/p2'])
        t.apply(xml_union_before,xml_union_patch,xml_union_out,format='xml')
        check('xml partial sibling union replay', xml_union_out.read_text()==xml_union_after.read_text())

        xml_residual_before=root/'xml-residual-before.xml'; xml_residual_after=root/'xml-residual-after.xml'; xml_residual_patch=root/'xml-residual-patch.yaml'
        write(xml_residual_before, '<root>\n  <abc>\n    <p1>false</p1>\n    <p2>old</p2>\n  </abc>\n</root>')
        write(xml_residual_after, '<root>\n  <abc>\n    <p1>true</p1>\n    <p2>new</p2>\n  </abc>\n</root>')
        t.compile(xml_residual_before,xml_residual_after,xml_residual_patch,format='xml')
        xml_residual_ops=load_one(xml_residual_patch).get('operations', [])
        check('xml different operations remain separate', len(xml_residual_ops)==2)

        # 18 explicit multi-path operations; every entry supports selectors
        paths_before=root/'paths-before.yaml'; paths_after=root/'paths-after.yaml'; paths_cfg=root/'paths-config.yaml'; paths_out=root/'paths-out.yaml'
        write(paths_before, '''
        app:
          p1: {enabled: false}
          p2: {enabled: false}
          p3: {enabled: false}
        ''')
        write(paths_after, '''
        app:
          p1: {enabled: true}
          p2: {enabled: true}
          p3: {enabled: true}
        ''')
        write(paths_cfg, '''
        version: 1
        operations:
          - op: set
            paths:
              - $/app/p1/enabled
              - $/app/[p2,p3]/enabled
            value: true
        ''')
        t.apply(paths_before,paths_cfg,paths_out,format='yaml')
        check('yaml paths entries support selectors', load_one(paths_out)==load_one(paths_after))

        auto_paths_before=root/'auto-paths-before.yaml'; auto_paths_after=root/'auto-paths-after.yaml'; auto_paths_patch=root/'auto-paths-patch.yaml'
        write(auto_paths_before, '''
        app:
          p1: {enabled: false}
          p2: {enabled: false}
          p3: {enabled: false}
        ''')
        write(auto_paths_after, '''
        app:
          p1: {enabled: true}
          p2: {enabled: true}
          p3: {enabled: false}
        ''')
        t.compile(auto_paths_before,auto_paths_after,auto_paths_patch,format='yaml')
        auto_paths_ops=load_one(auto_paths_patch).get('operations', [])
        check('auto config compresses unsafe wildcard fallback to exact union', len(auto_paths_ops)==1 and auto_paths_ops[0].get('path')=='$/app/[p1,p2]/enabled')

        # Final paths cleanup must still run after the optional optimizer budget
        # has expired, and it must render mapping fan-out as * and list fan-out
        # as [*].
        final_paths_compiler=DiffCompiler(optimization_timeout_seconds=0.01)
        final_paths_compiler._optimization_deadline=time.monotonic()-1
        dict_before={'a':{'b':{'p1':0,'p2':0,'p3':0}}}
        dict_after={'a':{'b':{'p1':1,'p2':1,'p3':1}}}
        dict_ops=[{'op':'replace','paths':['$/a/b/p1','$/a/b/p2','$/a/b/p3'],'value':1}]
        dict_final=final_paths_compiler._optimize_paths_to_single_path(dict_before,dict_after,dict_ops,final_pass=True)
        check('final paths compression survives exhausted budget for dict', dict_final[0].get('path')=='$/a/b/*')
        list_before={'a':{'items':[{'x':0},{'x':0},{'x':0}]}}
        list_after={'a':{'items':[{'x':1},{'x':1},{'x':1}]}}
        list_ops=[{'op':'replace','paths':['$/a/items/0/x','$/a/items/1/x','$/a/items/2/x'],'value':1}]
        list_final=final_paths_compiler._optimize_paths_to_single_path(list_before,list_after,list_ops,final_pass=True)
        check('final paths compression uses list wildcard', list_final[0].get('path')=='$/a/items/[*]/x')

        xml_paths_before=root/'xml-paths-before.xml'; xml_paths_after=root/'xml-paths-after.xml'; xml_paths_cfg=root/'xml-paths-config.yaml'; xml_paths_out=root/'xml-paths-out.xml'
        write(xml_paths_before, '<root><app><p1>false</p1><p2>false</p2><p3>false</p3></app></root>')
        write(xml_paths_after, '<root><app><p1>true</p1><p2>true</p2><p3>true</p3></app></root>')
        write(xml_paths_cfg, '''
        version: 1
        format: xml
        operations:
          - op: set
            paths:
              - /root/app/p1
              - /root/app/[p2,p3]
            value: "true"
        ''')
        t.apply(xml_paths_before,xml_paths_cfg,xml_paths_out,format='xml')
        check('xml paths entries support selectors', xml_paths_out.read_text().strip()==xml_paths_after.read_text().strip())

        # Optimizer safety budgets: stopping optimization must keep a verified config.
        budget_before=load_one(b); budget_after=load_one(a)
        budget_result=DiffCompiler(optimization_max_candidates=0).compile(budget_before,budget_after)
        check('yaml optimizer candidate limit keeps verified config', budget_result.verified and any('candidate limit' in warning for warning in budget_result.warnings))
        old_limit=os.environ.get('CONFIG_TOOL_OPTIMIZATION_MAX_CANDIDATES')
        try:
            os.environ['CONFIG_TOOL_OPTIMIZATION_MAX_CANDIDATES']='0'
            xml_budget=t.compile(xml_paths_before,xml_paths_after,root/'xml-budget-patch.yaml',format='xml')
            check('xml optimizer candidate limit keeps verified config', xml_budget.ok and any('candidate limit' in warning for warning in xml_budget.warnings))
        finally:
            if old_limit is None: os.environ.pop('CONFIG_TOOL_OPTIMIZATION_MAX_CANDIDATES',None)
            else: os.environ['CONFIG_TOOL_OPTIMIZATION_MAX_CANDIDATES']=old_limit

        # Large-mode regression: inner update_item lowering is checkpointed
        # before outer p1/p2 path generalization.  More than 500 operations must
        # finish with one replay-verified wildcard operation, not restart from the
        # original unoptimized list.
        large_n=260
        large_before={'a':{'b':{'c':{f'p{i}':[{'name':'A','x':0},{'name':'B','x':0}] for i in range(large_n)}}}}
        large_after={'a':{'b':{'c':{f'p{i}':[{'name':'A','x':1},{'name':'B','x':1}] for i in range(large_n)}}}}
        large_ops=[]
        for i in range(large_n):
            for name in ('A','B'):
                large_ops.append({'op':'update_item','path':f'$/a/b/c/p{i}','match':{'name':name},'set':{'x':1}})
        large_compiler=DiffCompiler(optimization_timeout_seconds=10, optimization_max_candidates=100)
        large_compiler._optimization_deadline=time.monotonic()+10
        large_compiler._optimization_session_active=True
        large_result=large_compiler._optimize_selectors(large_before,large_after,large_ops)
        check('large optimizer checkpoints inner before outer', len(large_result)==1 and large_result[0].get('path')=='$/a/b/c/*/[*]/x' and large_compiler._cached_replay(large_before,large_after,large_result))

        # Dependency-aware merge: identical current-version updates separated
        # by per-phase copy_item preparation must be moved after all copies and
        # merged without changing the copied source state.
        dep_before={'phases':{
            'p2':{'versions':[{'name':'v1','shadow':True,'cpu':'250m'}]},
            'f13p1':{'versions':[{'name':'v1','shadow':True,'cpu':'250m'}]},
        }}
        dep_original=[
            {'op':'copy_item','path':'$/phases/p2/versions','from':{'name':'v1'},'after':{'name':'v1'},'set':{'name':'v2'}},
            {'op':'update_item','path':'$/phases/p2/versions','match':{'name':'v1'},'set':{'shadow':False},'item_operations':[{'op':'replace','path':'$/cpu','value':'500m'}]},
            {'op':'copy_item','path':'$/phases/f13p1/versions','from':{'name':'v1'},'after':{'name':'v1'},'set':{'name':'v2'}},
            {'op':'update_item','path':'$/phases/f13p1/versions','match':{'name':'v1'},'set':{'shadow':False},'item_operations':[{'op':'replace','path':'$/cpu','value':'500m'}]},
        ]
        from yaml_config_engine.engine import YamlPatchEngine
        from copy import deepcopy
        dep_after=YamlPatchEngine().apply_document(deepcopy(dep_before),{'version':1,'operations':dep_original},track_no_effect=False)
        dep_compiler=DiffCompiler(optimization_timeout_seconds=5)
        dep_compiler._optimization_deadline=time.monotonic()+5
        dep_compiler._optimization_session_active=True
        dep_result=dep_compiler._optimize_dependency_aware_update_merges(dep_before,dep_after,dep_original)
        dep_updates=[op for op in dep_result if op.get('op')=='update_item']
        check('dependency-aware copy/update merge', len(dep_updates)==1 and (dep_updates[0].get('path')=='$/phases/*/versions' or dep_updates[0].get('paths')==['$/phases/p2/versions','$/phases/f13p1/versions']) and dep_compiler._cached_replay(dep_before,dep_after,dep_result))

        # Concise defaults profile: new Auto configs omit repeated safety fields,
        # while legacy configs retain their historical replace-all behavior.
        concise_before={'x':'abc25def'}
        concise_after={'x':'abc50def'}
        concise_result=DiffCompiler().compile(concise_before,concise_after)
        concise_op=concise_result.config['operations'][0]
        check('auto config emits concise defaults profile', concise_result.config.get('defaults_profile')=='concise-v1')
        check('auto config omits replace_value defaults', concise_op.get('op')=='replace_value' and 'count' not in concise_op and 'expect_replacements' not in concise_op)
        check('concise defaults replay', YamlPatchEngine().apply_document(deepcopy(concise_before),concise_result.config,track_no_effect=False)==concise_after)
        legacy_replace={'version':1,'operations':[{'op':'replace_value','path':'$/x','search':'a','replacement':'z'}]}
        concise_replace={'version':1,'defaults_profile':'concise-v1','operations':[{'op':'replace_value','path':'$/x','search':'a','replacement':'z'}]}
        check('legacy replace_value default compatibility', YamlPatchEngine().apply_document({'x':'aaa'},legacy_replace,track_no_effect=False)=={'x':'zzz'})
        check('concise replace_value defaults to one', YamlPatchEngine().apply_document({'x':'aaa'},concise_replace,track_no_effect=False)=={'x':'zaa'})

        # 10 CLI smoke
        cp=subprocess.run([sys.executable,'yaml_config_tool.py','verify',str(b),str(p),str(a)],capture_output=True,text=True)
        check('CLI verify smoke', cp.returncode == 0)

        # 11 format mismatch is explicit
        try: t.compile(b,xa,root/'bad.yaml')
        except ValueError as e: check('format mismatch validation', 'format mismatch' in str(e))
        else: raise AssertionError('format mismatch validation')

    print('SELF-TEST PASS')

if __name__ == '__main__': main()
