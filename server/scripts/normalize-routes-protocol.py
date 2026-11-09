"""Apply audited 0.429.2 Routes wire corrections without regenerating the base schema."""
from pathlib import Path
import re

def normalize_routes(text):
    """0.429.2 qualified enums and nullable pause case constants from C# dump."""
    # M20.2: Butterfly collector encounter writer 0x8962B1C, tag 0x31.
    text=re.sub(r'message ButterflyCollectorRewardEncounterProtoResponse \{.*?\n\}',lambda m:m[0].replace('uint64 encounter_id = 6;','fixed64 encounter_id = 6;'),text,count=1,flags=re.S)
    # M20.1: native writer 0x8A8612C and codecs 0x8A87EAC retain
    # retired tags. Sequential exporter numbers silently zero creator limits.
    creation_names='''max_open_routes min_total_distance_m max_total_distance_m max_name_length max_description_length min_player_level enabled enable_immediate_route_ingestion min_breadcrumb_distance_delta_meters creation_limit_window_days creation_limit_per_window creation_limit_window_offset_ms max_distance_from_anchor_pois_m algorithm simplification_tolerance max_distance_warning_distance_meters max_recording_speed_meters_per_second moderation_enabled client_breadcrumb_settings disabled_tags duration_distance_to_speed_multiplier duration_buffer_s minimum_distance_between_pins_m max_pin_distance_from_route_m interaction_range_meters max_client_map_panning_distance_meters resume_range_meters route_smoothing_params no_moderation_route_retry_threshold_ms no_moderation_route_reporting_threshold_ms route_path_edit_params max_breadcrumb_distance_delta_meters max_recall_count_threshold allowable_gps_drift_meters max_post_punishment_ban_time_ms max_submission_count_threshold pin_creation_enabled_for_route_creation show_submission_status_history elevation_tags route_elevation_settings allow_appeals allow_deleting_appealed_routes permitted_tags'''.split()
    creation_numbers=dict(zip(creation_names,[1,4,5,*range(14,28),*range(29,55)]))
    assert len(creation_numbers)==43
    text=re.sub(r'message RoutesCreationSettingsProto \{.*?\n\}',lambda m:re.sub(r'(\b(\w+)\s*=\s*)\d+(;)',lambda f:f[1]+str(creation_numbers[f[2]])+f[3] if f[2] in creation_numbers else f[0],m[0]),text,count=1,flags=re.S)
    # ChangePokemonFormProto writer 0x89C926C: fixed64 ID, enum target.
    text=re.sub(r'message ChangePokemonFormProto \{.*?\n\}',lambda m:re.sub(r'(?<![.\w])Form target_form','PokemonDisplayProto.Form target_form',m[0].replace('uint64 pokemon_id','fixed64 pokemon_id').replace('PokemonDisplayProto.PokemonDisplayProto.Form','PokemonDisplayProto.Form')),text,count=1,flags=re.S)
    text=re.sub(r'message FormChangeProto \{.*?\n\}',lambda m:m[0].replace('repeated Form available_form','repeated PokemonDisplayProto.Form available_form'),text,count=1,flags=re.S)
    # Native NPC fields are scoped enums, not exporter placeholder messages.
    for message in ('NpcEncounterProto','ClientDialogueLineProto'):
        text=re.sub(r'message '+message+r' \{.*?\n\}',lambda m:re.sub(r'(?<![.\w])InvasionCharacter character','EnumWrapper.InvasionCharacter character',re.sub(r'(?<![.\w])InvasionCharacterExpression expression','EnumWrapper.InvasionCharacterExpression expression',re.sub(r'(?<![.\w])Character map_character','QuestDialogProto.Character map_character',m[0]))),text,count=1,flags=re.S)
    text=text.replace('message RouteTab {}','message RouteTab { string title_string_id = 1; repeated string route_ids = 2; }')
    text=re.sub(r'message GetRoutesOutProto \{.*?\n\}',lambda m:m[0].replace('ClientRouteGetProto route_list = 2;','ClientRouteGetProto route_list = 4;').replace('Status status = 3;','Status status = 2;').replace('RouteTab route_tabs = 4;','RouteTab route_tabs = 3;'),text,count=1,flags=re.S)
    for name in ('StartRouteOutProto','ProgressRouteOutProto','CancelRouteOutProto'):
        text=re.sub(r'message '+name+r' \{.*?\n\}',lambda m:re.sub(r'(?<![.\w])Status status =', 'RoutePlayStatus.Status status =',m[0]),text,count=1,flags=re.S)
    text=re.sub(r'message AwardedRouteBadge \{.*?\n\}',lambda m:re.sub(r'(?<![.\w])BadgeLevel badge_level =','RouteBadgeLevel.BadgeLevel badge_level =',m[0]),text,count=1,flags=re.S)
    def badge_fields(match):
        value=match[0]
        for old,new in [('can_preview = 19','can_preview = 20'),('hidden = 20','hidden = 21'),('route = 21','route = 22'),('pins = 22','pins = 23'),('rated = 23','rated = 19')]:value=value.replace(old,new)
        if 'nullable_favorite' not in value:value=value[:-1]+'    oneof nullable_favorite { bool favorite = 25; }\n}'
        return value
    text=re.sub(r'message AwardedRouteBadge \{.*?\n\}',badge_fields,text,count=1,flags=re.S)
    text=re.sub(r'message ProgressRouteProto \{.*?\n\}',lambda m:re.sub(r'(?<![.\w])ActivityType activity_type =','RouteActivityType.ActivityType activity_type =',m[0]),text,count=1,flags=re.S)
    # Native WriteTo 0x8D5D780 writes AcquireReward with tag 0x38 (field 7).
    text=re.sub(r'message ProgressRouteProto \{.*?\n\}',lambda m:m[0].replace('bool acquire_reward = 5;','bool acquire_reward = 7;'),text,count=1,flags=re.S)
    for name,tag in [('ProgressRouteProto',6),('UpdateRouteDraftProto',4)]:
        text=re.sub(r'message '+name+r' \{.*?\n\}',lambda m:m[0] if 'nullable_pause' in m[0] else m[0][:-1]+'    oneof nullable_pause { bool pause = '+str(tag)+'; }\n}',text,count=1,flags=re.S)
    # Observed Pixel pause request and native writer: proposed draft is 2.
    text=text.replace('SharedRouteProto proposed_route_draft = 1;','SharedRouteProto proposed_route_draft = 2;')
    declaration='message RouteActivityType { enum ActivityType { ActivityType_UNSET = 0; ActivityType_NO_ACTIVITY = 1; ActivityType_ACTIVITY_POKEMON_TRADE = 2; ActivityType_ACTIVITY_POKEMON_COMPARE = 3; ActivityType_ACTIVITY_GIFT_TRADE = 4; } }'
    if 'message RouteActivityType {' not in text:text+='\n'+declaration+'\n'
    return text

if __name__ == "__main__":
    path=Path(__file__).resolve().parents[1]/"protocol/game.proto"
    path.write_text(normalize_routes(path.read_text(encoding="utf-8")),encoding="utf-8")
