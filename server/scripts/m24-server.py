"""Trace actual isolated M24 protocol traffic without supplying any outcomes."""
import argparse
import hashlib
import json
import logging
from pathlib import Path
import sys
import threading
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]
from google.protobuf.json_format import MessageToDict
from dittobuffer.gameplay.game import Game, p
from dittobuffer.gameplay import raid_transport
from dittobuffer.runtime.http import create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--calendar-file', help='Private M24 reward-calendar fixture, never the device/server clock')
    args = parser.parse_args()
    config, output = (ROOT/args.config).resolve(), (ROOT/args.evidence).resolve()
    settings = json.loads(config.read_text(encoding='utf8'))
    if (not config.is_relative_to(ROOT/'.private') or not output.is_relative_to(ROOT/'.private')
            or not (ROOT/settings['database']).resolve().is_relative_to(ROOT/'.private')
            or not settings.get('profile', '').startswith('m24-')):
        raise ValueError('Only an explicit isolated M24 profile may be traced')
    calendar=(ROOT/args.calendar_file).resolve() if args.calendar_file else None
    if calendar and (calendar.parent!=config.parent or not calendar.name.startswith('lab-calendar')):
        raise ValueError('Calendar must be a lab-calendar file beside the private config')
    def calendar_now():
        value=json.loads(calendar.read_text())['unix_ms']
        if type(value) is not int or not 1451606400000<=value<=4102444800000:raise ValueError('Invalid isolated reward date')
        return value
    output.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    def redact(value):
        if isinstance(value, dict):
            return {k: '[redacted]' if any(x in k.lower() for x in ('password', 'token', 'connection')) else redact(v) for k,v in value.items()}
        if isinstance(value, list):return [redact(v) for v in value]
        return value
    def record(event):
        with lock, (output/'events.jsonl').open('a', encoding='utf8') as stream:
            stream.write(json.dumps(redact(dict(at_ms=time.time_ns()//1000000, **event)), separators=(',', ':'))+'\n')
    original = Game.rpc
    def rpc(game, request, *values, **options):
        if calendar:
            game.reward_calendar_clock=calendar_now
            # New ordinary wild identity for each lab day, with real expiry.
            game.config['spawn_generation']=calendar_now()//86400000
        selected = {}
        for item in request.requests:
            if item.method not in (102,103,104,106,163,165,166,3010,406,1200,1202,1203,1204,1206,1207,1001,1002,101,980,5025,156,157,158,2,127,403,404,408):continue
            stem = ''.join(v.title() for v in game.methods.get(item.method, '').lower().split('_'))
            selected[item.method] = stem
            typ = p.ClaimCodenameRequestProto if item.method==403 else getattr(p, stem+'Proto', None)
            decoded = MessageToDict(typ.FromString(item.body), preserving_proto_field_name=True) if typ else {'bytes': len(item.body)}
            record(dict(direction='request', rpc_id=request.rpc_id, method=item.method, calendar_ms=calendar_now() if calendar else None, decoded=decoded))
        response = original(game, request, *values, **options)
        for item in response.payloads:
            if item.method not in selected:continue
            typ = p.CodenameResultProto if item.method==403 else getattr(p, selected[item.method]+'OutProto', None)
            decoded = MessageToDict(typ.FromString(item.payload), preserving_proto_field_name=True) if typ else {'bytes': len(item.payload)}
            if item.method == 106:
                packet=p.GetMapObjectsOutProto.FromString(item.payload)
                decoded = dict(status=decoded.get('status'), cells=len(packet.map_cell),
                    gyms=[dict(id=f.fort_id,revision=f.last_modified_ms,team=f.team,guard=f.guard_pokemon_id,
                        raid_seed=f.raid_info.raid_seed if f.HasField('raid_info') else None,
                        raid_end=f.raid_info.raid_end_ms if f.HasField('raid_info') else None)
                        for c in packet.map_cell for f in c.fort if f.fort_type==0])
            record(dict(direction='response', rpc_id=request.rpc_id, method=item.method, decoded=decoded))
        return response
    Game.rpc = rpc
    original_pbs = raid_transport.handle
    def pbs(game, path, headers, body=b''):
        if body:
            record(dict(direction='pbs-request', path=path, decoded=MessageToDict(p.SendBattleEventProto.FromString(body), preserving_proto_field_name=True)))
        result = original_pbs(game, path, headers, body)
        if isinstance(result, p.BattleStateOutProto):
            state = result.battle_state;actor = state.actors.get('raid-boss')
            pokemon = state.pokemon.get(actor.active_pokemon_id) if actor else None
            record(dict(direction='pbs-state', state=state.state,
                boss_hp=pokemon.resources[1].quantity if pokemon and 1 in pokemon.resources else None,
                events=[dict(type=e.type, serial=e.serial) for e in state.events]))
        return result
    raid_transport.handle = pbs
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s',
        handlers=[logging.FileHandler(output/'server.log', encoding='utf8'), logging.StreamHandler()])
    record(dict(event='source', sha256={str(v.relative_to(ROOT)):hashlib.sha256(v.read_bytes()).hexdigest()
        for v in [ROOT/'src/dittobuffer/gameplay/raids.py',ROOT/'src/dittobuffer/gameplay/raid_rewards.py']}))
    server = create_server(args.config)
    try:server.serve_forever()
    finally:server.server_close()


if __name__ == '__main__':main()
