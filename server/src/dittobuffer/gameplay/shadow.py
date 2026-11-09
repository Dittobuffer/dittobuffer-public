"""Shadow purification metadata for the supplied Game Master snapshot.

The compact snapshot preserves model/form data but omits each Pokemon's
``ShadowAttributesProto`` (field 45).  Without it the native client has no
purification cost and cannot show the Purify action, so append the local
server's costs to every Pokemon template.  The same costs are used by
``core.management.purify``.
"""
from protocol.opaque import byte_field, fields
from dittobuffer.gameplay.game import p

FRUSTRATION = 322
RETURN = 323


def costs(config):
    stardust = max(0, int(config.get('purify_stardust_cost', 1000)))
    candy = max(0, int(config.get('purify_candy_cost', 25)))
    return stardust, candy


def shared_move_multipliers(config):
    """Cost multipliers for a Shadow/Purified second Charged Attack."""
    return (
        float(config.get('shadow_third_move_stardust_multiplier', 1.2)),
        float(config.get('shadow_third_move_candy_multiplier', 1.2)),
        float(config.get('purified_third_move_stardust_multiplier', 0.8)),
        float(config.get('purified_third_move_candy_multiplier', 0.8)),
    )


def augment_template(raw, config):
    if not config.get('shadow', {}).get('enabled', True):
        return raw
    outer = list(fields(raw))
    data = next((value for number, kind, _, value in outer if number == 2 and kind == 2), None)
    if data is None:
        return raw
    values = {number: value for number, kind, _, value in fields(data)}
    if 45 in values:
        # A future snapshot may already carry its own authoritative costs.
        return raw
    stardust, candy = costs(config)
    attributes = p.ShadowAttributesProto(
        purification_stardust_needed=stardust,
        purification_candy_needed=candy,
        purified_charge_move=RETURN,
        shadow_charge_move=FRUSTRATION,
    )
    data += byte_field(45, attributes.SerializeToString())
    return b''.join(byte_field(2, data) if number == 2 and kind == 2 else wire
                    for number, kind, wire, value in outer)
