"""Run an isolated M18.2 listener with redacted protobuf evidence, not mock outcomes."""
from pathlib import Path
import argparse,json,logging,os,re,sys,threading,time
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from google.protobuf.json_format import MessageToDict
from dittobuffer.gameplay.game import Game,p
from dittobuffer.gameplay import max_battle
from dittobuffer.runtime.http import create_server

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--evidence',required=True)
    args=parser.parse_args()
    config=(ROOT/args.config).resolve();out=(ROOT/args.evidence).resolve()
    settings=json.loads(config.read_text(encoding='utf-8'))
    database=(ROOT/settings['database']).resolve()
    if settings.get('profile')!='m182-power-spots' or not database.is_relative_to(ROOT/'.private') or not out.is_relative_to(ROOT/'.private'):
        raise ValueError('Explicit isolated M18.2 private paths required')
    out.mkdir(parents=True,exist_ok=True)
    lock=threading.Lock()
    def redacted(value):
        if isinstance(value,dict):return {k:'[redacted]' if re.search('token|password|credential|authorization|rvn_connection',k,re.I) else redacted(v) for k,v in value.items()}
        if isinstance(value,list):return [redacted(v) for v in value]
        return value
    def record(value):
        with lock,(out/'rpc-events.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(redacted({'at_ms':time.time_ns()//1000000,**value}),separators=(',',':'))+'\n')
    def decoded(raw,name):
        kind=getattr(p,name,None)
        if kind is None:return {'protobuf_type_unavailable':name,'wire_hex':raw.hex()}
        try:return MessageToDict(kind.FromString(raw),preserving_proto_field_name=True)
        except Exception as error:return {'protobuf_type':name,'decode_error':type(error).__name__,'wire_hex':raw.hex()}
    original_rpc=Game.rpc
    def rpc(game,request,*args,**kwargs):
        selected=[]
        for item in request.requests:
            if item.method not in settings['rpc_trace_methods']:continue
            name=game.methods.get(item.method,'unknown')
            stem=''.join(word.title() for word in name.lower().split('_'))
            if item.method==2462:stem='GetStationedPokemonDetails'
            selected.append((item.method,stem))
            record({'direction':'request','rpc_id':request.rpc_id,'method':item.method,'name':name,
                    'decoded':decoded(item.body,stem+'Proto')})
        try:response=original_rpc(game,request,*args,**kwargs)
        except Exception as error:
            record({'direction':'exception','rpc_id':request.rpc_id,'methods':selected,
                    'type':type(error).__name__,'error':str(error)})
            raise
        for item in response.payloads:
            stem=next((stem for method,stem in selected if method==item.method),None)
            if stem:
                payload=decoded(item.payload,stem+'OutProto')
                if item.method==106:payload={'status':payload.get('status'),'cells':len(payload.get('map_cell',[])),
                    'stations':[station for cell in payload.get('map_cell',[]) for station in cell.get('stations',[])]}
                record({'direction':'response','rpc_id':request.rpc_id,'method':item.method,'decoded':payload})
        return response
    Game.rpc=rpc
    original_pbs=max_battle.handle
    def pbs(game,path,headers,body=b''):
        if body:record({'direction':'pbs-request','path':path.split('?',1)[0],
                        'decoded':decoded(body,'SendBattleEventProto')})
        response=original_pbs(game,path,headers,body)
        record({'direction':'pbs-response','path':path.split('?',1)[0],
                'decoded':MessageToDict(response,preserving_proto_field_name=True)})
        return response
    max_battle.handle=pbs
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s',
        handlers=[logging.FileHandler(out/'server.log',encoding='utf-8'),logging.StreamHandler()])
    with create_server(config.relative_to(ROOT).as_posix()) as server:
        (out/'worker.json').write_text(json.dumps({'pid':os.getpid(),'config':str(config),
            'root':str(ROOT),'started_at':time.time()},indent=2),encoding='utf-8')
        print('Isolated M18.2 real listener ready',flush=True)
        server.serve_forever()

if __name__=='__main__':main()
