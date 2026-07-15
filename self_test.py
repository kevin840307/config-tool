from __future__ import annotations
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess, sys, textwrap
from config_tool_api import ConfigTool


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

        # 10 CLI smoke
        cp=subprocess.run([sys.executable,'yaml_config_tool.py','verify',str(b),str(p),str(a)],capture_output=True,text=True)
        check('CLI verify smoke', cp.returncode == 0)

        # 11 format mismatch is explicit
        try: t.compile(b,xa,root/'bad.yaml')
        except ValueError as e: check('format mismatch validation', 'format mismatch' in str(e))
        else: raise AssertionError('format mismatch validation')

    print('SELF-TEST PASS')

if __name__ == '__main__': main()
