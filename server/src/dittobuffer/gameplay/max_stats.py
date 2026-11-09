"""Project earned Max history into the client's existing inventory statistics.

Native StationInteractionGuiController uses these counters before it marks its
own won-battle tutorial. This module never grants a tutorial or a victory.
"""
import json

from google.protobuf.message import DecodeError
from dittobuffer.gameplay.game import p
from dittobuffer.gameplay.max_system import enabled


def room_mode(raw):
    """Read frozen attempt identity; never infer an old boss from today's pool."""
    if not raw:return 0
    try:
        state=json.loads(raw)
        if 'boss_mode' in state:return state['boss_mode']
        if state.get('boss'):
            return p.PokemonProto.FromString(bytes.fromhex(state['boss']['pokemon'])).pokemon_display.bread_mode_enum
        if state.get('station_snapshot'):
            return p.StationProto.FromString(bytes.fromhex(state['station_snapshot'])).battle_details.battle_pokemon.pokemon_display.bread_mode_enum
    except (ValueError,TypeError,KeyError,DecodeError):
        # The durable win still counts. Unknown historical mode cannot justify
        # claiming a Gigantamax-specific win or consulting a changed station.
        return 0
    return 0


def apply(game,db,player,stats):
    if not enabled(game):return
    stats.num_bread_battles_entered=db.execute(
        "SELECT COUNT(*) FROM max_members WHERE player_id=? AND party<>'[]'",(player,)).fetchone()[0]
    wins=db.execute('SELECT r.state FROM max_victories v LEFT JOIN max_rooms r ON r.id=v.room_id WHERE v.player_id=?',(player,)).fetchall()
    stats.num_bread_battles_won=len(wins)
    stats.num_bread_battles_dough_won=sum(room_mode(row['state']) in (2,3) for row in wins)
