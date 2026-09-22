#!/usr/bin/env python3
import argparse, hashlib, json, pathlib, subprocess, tempfile, urllib.request, zipfile
ROOT = pathlib.Path(__file__).resolve().parents[1]
SUPPORT = ['install.sh', 'verify_manifest.py', 'artifacts.lock.json']
sha = lambda b: hashlib.sha256(b).hexdigest()
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output',type=pathlib.Path,default=ROOT/'dist/VN300_Kvaser_6.18.50_rpi-v8_arm64.zip'); ap.add_argument('--cache',type=pathlib.Path,default=ROOT/'build/kvaser-cache'); ap.add_argument('--no-download',action='store_true'); a=ap.parse_args()
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    files={p:subprocess.check_output(['git','show',f'{commit}:packaging/kvaser-addon/{p}'],cwd=ROOT) for p in SUPPORT}
    lock=json.loads(files['artifacts.lock.json']); a.cache.mkdir(parents=True,exist_ok=True)
    for item in lock['artifacts']:
        cache=a.cache/pathlib.Path(item['path']).name
        if not cache.exists():
            if a.no_download: raise SystemExit(f'Missing {cache}')
            cache.write_bytes(urllib.request.urlopen(item['url'],timeout=120).read())
        data=cache.read_bytes()
        if sha(data)!=item['sha256']: raise SystemExit(f'Hash mismatch: {item["path"]}')
        files[item['path']]=data
    files['BUILD.json']=(json.dumps({'source_commit':commit,'target':lock['target'],'artifact_count':len(lock['artifacts'])},indent=2)+'\n').encode()
    files['MANIFEST.sha256']=''.join(f'{sha(v)}  {k}\n' for k,v in sorted(files.items())).encode()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix='.zip') as t:
        with zipfile.ZipFile(t.name,'w',zipfile.ZIP_DEFLATED) as z:
            for name,data in sorted(files.items()):
                info=zipfile.ZipInfo('VN300_Kvaser_Addon/'+name,(2026,1,1,0,0,0)); info.external_attr=(0o100755 if name.endswith('.sh') else 0o100644)<<16; info.compress_type=zipfile.ZIP_DEFLATED; z.writestr(info,data)
        with zipfile.ZipFile(t.name) as z: assert z.testzip() is None
        a.output.write_bytes(pathlib.Path(t.name).read_bytes())
    digest=sha(a.output.read_bytes()); a.output.with_suffix('.zip.sha256').write_text(f'{digest}  {a.output.name}\n'); print(json.dumps({'zip':str(a.output.resolve()),'sha256':digest,'commit':commit},indent=2))
if __name__=='__main__': main()
