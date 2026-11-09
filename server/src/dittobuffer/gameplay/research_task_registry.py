"""Shared native task registry; only transactional gameplay adapters are enabled."""
TASKS = {
 'super_effective_charge':('Use super-effective Charged Attacks',53),
 'gym_win':('Win Gym battles',7),'pvp_win':('Win Trainer battles',27),'mega_evolve':('Mega Evolve Pokemon',43),'buddy_heart':('Earn Buddy hearts',34),'trade':('Receive Pokemon by trade',23),'walk':('Walk verified kilometres',42),'zygarde_cell':('Collect Zygarde Cells',66),
 'hatch':('Hatch eggs',6),'evolve':('Evolve Pokemon',15),'buddy_candy':('Earn Buddy Candy',17),'power_up':('Power up Pokemon',14),
 'transfer':('Transfer Pokemon',10),'gift_send':('Send Gifts',24),'gift_open':('Open Gifts',59),'postcard_pin':('Pin Postcards',95),
 'catch':('Catch Pokemon',4),'spin':('Spin PokeStops',5),'throw':('Make throws',16),
 'raid':('Win raids',8),'route':('Complete Routes',65),
 'rocket_grunt':('Defeat Rocket Grunts',29),'rocket_leader':('Defeat Rocket Leaders',29),
 'rocket_boss':('Defeat Giovanni',29),'shadow_catch':('Catch Shadow Pokemon',4),
 'purify':('Purify Pokemon',30),'radar_assemble':('Assemble Rocket Radars',41),
 'max_collect':('Collect Max Particles',82),'max_loot':('Visit Power Spots',83),
 'max_win':('Win Max Battles',84),'max_unlock':('Unlock Max Moves',86),'max_upgrade':('Upgrade Max Moves',87),
}
UNAVAILABLE_TASKS = ['unique_spin','explore_area','buddy_feed','party_challenge']
