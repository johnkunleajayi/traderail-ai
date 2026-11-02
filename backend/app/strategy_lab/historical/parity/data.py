"""Checksummed public archive acquisition and validated local normalization.
Acquisition is explicit CLI-only. Replay never imports or calls the downloader.
"""
import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL
from app.market_data.binance import decimal_value
from app.market_intelligence.types import Candle, DatasetContext
from app.market_intelligence.validation import validate_dataset
from app.strategy_lab.historical.report import json_values

HOUR=timedelta(hours=1)
EPOCH=datetime(1970,1,1,tzinfo=timezone.utc)
CONTEXT=DatasetContext(uuid5(NAMESPACE_URL,'research:BINANCE:SPOT:BTC/USDT'),'BTC/USDT','BINANCE','1h')
DEFAULT_MAX_CANDLES=20000
HARD_MAX_CANDLES=100000
MAX_BYTES=64*1024*1024


def bounded_limit(value):
    if isinstance(value,bool) or not isinstance(value,int) or not 100<=value<=HARD_MAX_CANDLES:
        raise ValueError('Candle limit outside supported bounds')
    return value


def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(65536),b''):h.update(block)
    return h.hexdigest()


def write_json(path,value):
    with Path(path).open('x',encoding='utf-8') as f:json.dump(json_values(value),f,indent=2,allow_nan=False)


def download(months,directory):
    """Operator-approved public-data-only download; finite, no retries or redirects."""
    import httpx
    months=tuple(months)
    if not 1<=len(months)<=24 or len(set(months))!=len(months):raise ValueError('One to 24 distinct months required')
    now=datetime.now(timezone.utc)
    for month in months:
        if not re.fullmatch(r'20[0-9]{2}-(0[1-9]|1[0-2])',month):raise ValueError('Invalid archive month')
        if month>=now.strftime('%Y-%m'):raise ValueError('Only completed archival months supported')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    manifest={'format':'binance-spot-archives-v1','status':'INCOMPLETE','source':'Binance Public Data',
        'documentation':'https://github.com/binance/binance-public-data',
        'license':'DATA_LICENSE_NOT_DETERMINED; verify redistribution rights separately',
        'venue':'BINANCE_SPOT','symbol':'BTCUSDT','canonical_symbol':'BTC/USDT','timeframe':'1h','timezone':'UTC','files':[]}
    try:
        with httpx.Client(timeout=httpx.Timeout(20,connect=5),follow_redirects=False,trust_env=False) as client:
            for month in sorted(months):
                name='BTCUSDT-1h-'+month+'.zip'
                url='https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/'+name
                checksum=client.get(url+'.CHECKSUM');checksum.raise_for_status()
                if len(checksum.content)>2048:raise ValueError('Oversized checksum')
                expected=checksum.text.split()[0]
                if not re.fullmatch(r'[a-fA-F0-9]{64}',expected):raise ValueError('Invalid checksum')
                target=directory/name
                with client.stream('GET',url) as response:
                    response.raise_for_status();total=0
                    with target.open('xb') as f:
                        for block in response.iter_bytes():
                            total+=len(block)
                            if total>MAX_BYTES:raise ValueError('Oversized archive')
                            f.write(block)
                if file_hash(target)!=expected.lower():raise ValueError('Archive checksum mismatch')
                (directory/(name+'.CHECKSUM')).write_bytes(checksum.content)
                manifest['files'].append({'file':name,'url':url,'sha256':expected.lower(),
                    'retrieved_at':datetime.now(timezone.utc).isoformat(),
                    'timestamp_unit':'us' if month>='2025-01' else 'ms'})
        manifest['status']='CHECKSUM_VERIFIED'
    finally:write_json(directory/'archives.json',manifest)
    return manifest



def safe_manifest(value):
    """Only fixed public-source metadata may propagate into research reports."""
    fixed={'format':'binance-spot-archives-v1','status':'CHECKSUM_VERIFIED',
        'source':'Binance Public Data','documentation':'https://github.com/binance/binance-public-data',
        'license':'DATA_LICENSE_NOT_DETERMINED; verify redistribution rights separately',
        'venue':'BINANCE_SPOT','symbol':'BTCUSDT','canonical_symbol':'BTC/USDT','timeframe':'1h','timezone':'UTC'}
    if not isinstance(value,dict) or set(value)-set(fixed)-{'files'}:raise ValueError('Unsupported source metadata')
    if value.get('format')!=fixed['format'] or value.get('status')!=fixed['status']:raise ValueError('Verified source manifest required')
    if any(value.get(k,v)!=v for k,v in fixed.items()):raise ValueError('Unsupported source identity')
    files=value.get('files')
    if not isinstance(files,list) or not 1<=len(files)<=24:raise ValueError('Bounded archive list required')
    for source in files:
        if set(source)!={'file','url','sha256','retrieved_at','timestamp_unit'}:raise ValueError('Unsupported archive metadata')
        name=source['file']
        if not isinstance(name,str) or not re.fullmatch(r'BTCUSDT-1h-20[0-9]{2}-(0[1-9]|1[0-2])\.zip',name):raise ValueError('Unsupported archive name')
        if source['url']!='https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/'+name:raise ValueError('Unsupported public URL')
        if not re.fullmatch(r'[a-f0-9]{64}',source['sha256']):raise ValueError('Invalid SHA256')
        at=datetime.fromisoformat(source['retrieved_at'])
        if at.tzinfo is None or at.utcoffset()!=timedelta(0):raise ValueError('UTC retrieval required')
        if source['timestamp_unit'] not in ('ms','us'):raise ValueError('Timestamp unit required')
    return {**fixed,'files':files}

def normalize(manifest_path,directory,*,max_candles=DEFAULT_MAX_CANDLES):
    limit=bounded_limit(max_candles);manifest_path=Path(manifest_path)
    if manifest_path.stat().st_size>100000:raise ValueError('Oversized manifest')
    manifest=safe_manifest(json.loads(manifest_path.read_text(encoding='utf-8')))
    if manifest.get('format')!='binance-spot-archives-v1' or manifest.get('status')!='CHECKSUM_VERIFIED':raise ValueError('Verified archive manifest required')
    if not 1<=len(manifest['files'])<=24:raise ValueError('Archive count bound')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    counts=Counter();errors=[];previous=None;first=last=None;cutoff=None;total=0
    def reject(code,line):
        counts[code]+=1
        if len(errors)<100:errors.append({'row':line,'code':code})
    try:
        with (directory/'candles.jsonl').open('x',encoding='utf-8') as output:
            for source in manifest['files']:
                name=source['file']
                if Path(name).name!=name or not re.fullmatch(r'BTCUSDT-1h-20[0-9]{2}-[0-9]{2}\.zip',name):raise ValueError('Unsafe archive filename')
                expected_url='https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/'+name
                if source['url']!=expected_url:raise ValueError('Unexpected archive source')
                path=manifest_path.parent/name
                if path.stat().st_size>MAX_BYTES or file_hash(path)!=source['sha256']:raise ValueError('Raw archive hash mismatch')
                retrieved=datetime.fromisoformat(source['retrieved_at'])
                if retrieved.tzinfo is None or retrieved.utcoffset()!=timedelta(0):raise ValueError('UTC retrieval required')
                cutoff=max(cutoff,retrieved) if cutoff else retrieved
                unit=source['timestamp_unit']
                expected_unit='us' if name[11:18]>='2025-01' else 'ms'
                if unit!=expected_unit:raise ValueError('Unexpected source timestamp unit')
                micros=1000 if unit=='ms' else 1
                with zipfile.ZipFile(path) as archive:
                    members=archive.infolist()
                    if len(members)!=1 or members[0].file_size>MAX_BYTES:raise ValueError('Bounded single CSV archive required')
                    with archive.open(members[0]) as binary:
                        for row in csv.reader(io.TextIOWrapper(binary,encoding='utf-8-sig',newline='')):
                            total+=1
                            if total>limit:raise ValueError('Candle count limit exceeded')
                            try:
                                if len(row)!=12:raise ValueError('schema')
                                start=EPOCH+timedelta(microseconds=int(row[0])*micros)
                                end=EPOCH+timedelta(microseconds=int(row[6])*micros)
                                if start.strftime('%Y-%m')!=name[11:18]:raise ValueError('month')
                                if end!=start+HOUR-timedelta(microseconds=micros):raise ValueError('interval')
                                if start+HOUR>retrieved:
                                    reject('INCOMPLETE_EXCLUDED',total);continue
                                candle=Candle(CONTEXT.instrument_id,'BINANCE','1h',start,start+HOUR-timedelta(milliseconds=1),
                                    *(decimal_value(row[i]) for i in (1,2,3,4,5)),retrieved)
                                validate_dataset(CONTEXT,(candle,),retrieved)
                                if previous is not None:
                                    if start==previous:reject('DUPLICATE',total);continue
                                    if start<previous:reject('OUT_OF_ORDER',total);continue
                                    if start!=previous+HOUR:
                                        reject('MISSING_INTERVAL',total)
                                        counts['missing_intervals']+=(start-previous)//HOUR-1
                                output.write(json.dumps(json_values(candle),separators=(',',':'))+'\n')
                                previous=last=start;first=first or start;counts['accepted']+=1
                            except (ValueError,ArithmeticError,OverflowError,TypeError):reject('INVALID_ROW',total)
        usable=counts['accepted']>=100 and not any(counts[k] for k in ('INVALID_ROW','DUPLICATE','OUT_OF_ORDER','MISSING_INTERVAL'))
        result={'format':'traderail-parity-dataset-v1','usable':usable,'source_kind':'PUBLIC_BINANCE_ARCHIVE',
            'source_manifest':manifest,'context':json_values(CONTEXT),'raw_manifest_sha256':file_hash(manifest_path),
            'normalized_sha256':file_hash(directory/'candles.jsonl'),'counts':dict(counts),'rejected_examples':errors,
            'as_of':cutoff,'first_open':first,'last_open':last,'max_candles':limit,
            'timestamp_semantics':'UTC open; source interval validated; close normalized to inclusive last millisecond; original raw archive retained'}
    except Exception:
        write_json(directory/'dataset.json',{'format':'traderail-parity-dataset-v1','usable':False,'status':'NORMALIZATION_FAILED','counts':dict(counts)})
        raise
    write_json(directory/'dataset.json',result)
    return result


def load_dataset(directory,*,max_candles=DEFAULT_MAX_CANDLES):
    """Bounded local snapshot; no network, sorting, interpolation or repair."""
    limit=bounded_limit(max_candles);directory=Path(directory)
    if (directory/'dataset.json').stat().st_size>200000:raise ValueError('Oversized dataset metadata')
    info=json.loads((directory/'dataset.json').read_text(encoding='utf-8'))
    allowed_keys={'format','usable','source_kind','source_manifest','context','raw_manifest_sha256','normalized_sha256','counts','rejected_examples','as_of','first_open','last_open','max_candles','timestamp_semantics'}
    if set(info)!=allowed_keys:raise ValueError('Unsupported dataset metadata')
    if info['source_kind']!='PUBLIC_BINANCE_ARCHIVE':raise ValueError('Unsupported dataset source')
    if info['timestamp_semantics']!='UTC open; source interval validated; close normalized to inclusive last millisecond; original raw archive retained':raise ValueError('Unsupported timestamp metadata')
    codes={'accepted','INCOMPLETE_EXCLUDED','INVALID_ROW','DUPLICATE','OUT_OF_ORDER','MISSING_INTERVAL','missing_intervals'}
    if set(info['counts'])-codes or any(type(v) is not int or v<0 or v>HARD_MAX_CANDLES for v in info['counts'].values()):raise ValueError('Invalid integrity counts')
    if any(info['counts'].get(k,0) for k in ('INVALID_ROW','DUPLICATE','OUT_OF_ORDER','MISSING_INTERVAL')):raise ValueError('Rejected source cannot be evaluated')
    if len(info['rejected_examples'])>100 or any(set(e)!={'row','code'} or e['code'] not in codes or type(e['row']) is not int for e in info['rejected_examples']):raise ValueError('Invalid rejection metadata')
    for key in ('as_of','first_open','last_open'):
        moment=datetime.fromisoformat(info[key])
        if moment.tzinfo is None or moment.utcoffset()!=timedelta(0):raise ValueError('UTC coverage required')
    if not re.fullmatch(r'[a-f0-9]{64}',info['raw_manifest_sha256']):raise ValueError('Manifest hash required')
    bounded_limit(info['max_candles'])
    info['source_manifest']=safe_manifest(info['source_manifest'])
    if info.get('format')!='traderail-parity-dataset-v1' or info.get('usable') is not True:raise ValueError('Usable integrity-checked dataset required')
    path=directory/'candles.jsonl'
    if path.stat().st_size>MAX_BYTES or file_hash(path)!=info['normalized_sha256']:raise ValueError('Normalized hash mismatch')
    from app.strategy_lab.historical.input import LocalCandle
    if info['context'].get('instrument_id')!=str(CONTEXT.instrument_id):raise ValueError('Dataset instrument identity mismatch')
    context=DatasetContext(**{**info['context'],'instrument_id':CONTEXT.instrument_id})
    if context!=CONTEXT:raise ValueError('Unsupported research context')
    rows=[]
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if len(rows)>=limit or len(line)>4096:raise ValueError('Candle/line bound exceeded')
            raw=json.loads(line)
            if (raw.pop('instrument_id'),raw.pop('provider'),raw.pop('timeframe'))!=(str(CONTEXT.instrument_id),'BINANCE','1h'):raise ValueError('Candle context mismatch')
            row=LocalCandle.model_validate(raw)
            rows.append(Candle(CONTEXT.instrument_id,'BINANCE','1h',**row.model_dump()))
    at=datetime.fromisoformat(info['as_of']);validate_dataset(context,rows,at)
    if len(rows)!=info['counts']['accepted'] or any(c.retrieved_at>at for c in rows):raise ValueError('Dataset count/cutoff mismatch')
    return context,tuple(rows),info
