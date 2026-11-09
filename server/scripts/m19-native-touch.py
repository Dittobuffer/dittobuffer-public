"""Pixel-only actual touch driver. Read-only isolated battle observation.

Never calls gameplay RPCs, edits HP, writes SQLite, or supplies battle outcomes.
"""
import os, shutil
import argparse,json,sqlite3,subprocess,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"src"))
sys.path.insert(0,str(ROOT/"src"/"dittobuffer"/"gameplay"))
sys.path.insert(0,str(ROOT/"protocol"))
from dittobuffer.gameplay.pvp_rules import source,MOVES


def game_activity(activity):
    return any(('mResumedActivity' in line or 'topResumedActivity' in line)
        and 'org.librenlp.pokemongo.dev/' in line and 'UnityMainActivity' in line
        for line in activity.splitlines())


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--serial',required=True)
    ap.add_argument('--battle',required=True)
    ap.add_argument('--output',required=True)
    ap.add_argument('--seconds',type=int,default=240)
    ap.add_argument('--style',choices=['normal','defensive'],default='normal')
    ap.add_argument('--switch-after-shield',action='store_true')
    ap.add_argument('--adb',default=os.environ.get('ADB') or shutil.which('adb') or 'adb');args=ap.parse_args()
    output=(ROOT/args.output).resolve()
    if not output.is_relative_to(ROOT/'builds') or output.exists():raise ValueError('Fresh evidence output required')
    cfg=json.loads((ROOT/'builds/m19-pvp.json').read_text())
    if (cfg.get('profile'),cfg.get('database'),cfg.get('port'))!=('m19-pvp','builds/m19-pvp-v2.sqlite3',8447):raise ValueError('Isolated M19 only')
    adb=[args.adb,'-s',args.serial]
    bootstrap=json.loads(subprocess.check_output(adb+['exec-out','su','-c','cat /data/user/0/org.librenlp.pokemongo.dev/files/librenlp-bootstrap.json']))
    if bootstrap.get('fixture_profile')!='m19-pvp':raise ValueError('Expected isolated native process')
    declaration=json.loads((ROOT/'builds/m19-fixture-declaration.json').read_text())
    player=next(v['player_id'] for v in declaration['accounts'] if v['key']=='alice')
    def read():
        with sqlite3.connect((ROOT/cfg['database']).as_uri()+'?mode=ro',uri=True) as db:
            row=db.execute('SELECT b.state,m.side FROM pvp_battles b JOIN pvp_members m ON m.battle_id=b.id WHERE b.id=? AND m.player_id=?',(args.battle,player)).fetchone()
            if not row:raise ValueError('Declared Alice battle missing')
            return json.loads(row[0]),row[1]
    def tap(x,y):subprocess.run(adb+['shell','input','tap',str(x),str(y)],check=True,stdout=subprocess.DEVNULL)
    end=time.monotonic()+max(10,min(300,args.seconds));last_charge=None;last_phase=None;charge_seen=0;shots=0;mid_shot=False;last_guard=0
    with output.open('x',encoding='utf8') as stream:
        def log(event,**values):
            row=dict(verification='NATIVE_TOUCH_WITH_READ_ONLY_FIXTURE_OBSERVATION',event=event,at_ms=int(time.time()*1000),**values)
            stream.write(json.dumps(row)+'\n');stream.flush();print(json.dumps(row),flush=True)
        def shot(label):
            nonlocal shots
            raw=subprocess.check_output(adb+['exec-out','screencap','-p'])
            if not raw.startswith(b'\x89PNG'):raise ValueError('Native gameplay screen unavailable; stopping touches')
            path=output.with_name(output.stem+'-'+str(shots).zfill(2)+'-'+label+'.png');shots+=1
            if path.exists():raise ValueError('Evidence collision')
            path.write_bytes(raw);log('screenshot',path=path.relative_to(ROOT).as_posix())
        while time.monotonic()<end:
            if time.monotonic()-last_guard>3:
                activity=subprocess.check_output(adb+['shell','dumpsys','activity','activities']).decode(errors='replace')
                if not game_activity(activity):
                    log('stopped_non_game_activity');return
                last_guard=time.monotonic()
            state,side=read();member=state['members'][side];actor=member['party'][member['active']]
            if state['phase']!=last_phase:
                log('phase',phase=state['phase'],hp=actor['hp'],energy=actor['energy']);last_phase=state['phase']
            if state['phase'] in (6,7,8):shot('terminal');log('authoritative_terminal',winner=state['winner'],reason=state.get('reason'));return
            if state['phase'] in (1,2):time.sleep(.25);continue
            c=state.get('charge')
            if c:
                if last_charge!=c['end_ms']:
                    last_charge=c['end_ms'];charge_seen=time.monotonic();mid_shot=False;shot('charge-start')
                    log('charge',attacker='alice' if c['side']==side else 'peer',shields=member['shields'])
                if time.monotonic()-charge_seen<1.2:time.sleep(.15);continue
                if not mid_shot and time.monotonic()-charge_seen>=2.5:
                    mid_shot=True;shot('native-minigame' if c['side']==side else 'native-shield-choice')
                if c['side']==side:
                    subprocess.run(adb+['shell','input','swipe','220','1750','850','1180','150'],check=True,stdout=subprocess.DEVNULL)
                    subprocess.run(adb+['shell','input','swipe','850','1750','220','1180','150'],check=True,stdout=subprocess.DEVNULL)
                else:
                    # Native defensive UI button. The backend validates the
                    # client's actual defensive-finish message and stock.
                    tap(540,1900)
                time.sleep(.1);continue
            if actor['hp']<=0:
                living=[v for i,v in enumerate(member['party']) if i!=member['active'] and v['hp']>0]
                if living:
                    tap(365 if len(living)>1 else 540,1980)
                    if len(living)==1:tap(365,1980)
                time.sleep(.2);continue
            mon=source(actor)
            switched=any(v['type']=='switch' and v['side']==side and v.get('voluntary') for v in state['history'])
            if args.switch_after_shield and member['shields']<2 and not switched:
                tap(990,1455)
            elif actor['energy']>=-MOVES[mon.move2]['energy_delta'] and (args.style!='defensive' or member['shields']<2):
                tap(540,2060)
            else:tap(540,1400)
            time.sleep(.12)
        log('bounded_stop_without_result_change')


if __name__=='__main__':main()
