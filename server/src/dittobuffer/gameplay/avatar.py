"""A local appearance using default articles in the client's avatar catalog."""

def legacy_default_avatar(p):
    avatar=p.PlayerNeutralAvatarProto(neutral_avatar_legacy_mapping_version=1)
    # Exact keys verified in the avatar catalog downloaded by this client.
    # The APK fallback articles render with incompatible magenta materials.
    for slot,article in [('hair','n_hair_default_a_0'),('shirt','n_shirt_default_a_0'),
                         ('pants','n_pants_default_a_0'),('shoes','n_shoes_default_a_0')]:
        getattr(avatar.articles,slot).article_id=article
    avatar.head_selection.selection=8  # LEGACYMALE, from this client's enum.
    for field in ('nose_selection','ear_selection','mouth_selection','eye_selection'):
        getattr(avatar,field).selection=1
    avatar.body_blend.size=0.5
    avatar.body_blend.musculature=0.5
    avatar.body_blend.hips=0.5
    avatar.body_blend.shoulders=0.5
    for field in avatar.face_positions.DESCRIPTOR.fields:
        setattr(avatar.face_positions,field.name,0.5)
    for field,color in [('skin_gradient',(0.72,0.48,0.32)),('hair_gradient',(0.12,0.07,0.04)),
                        ('eye_gradient',(0.15,0.25,0.35)),('facial_hair_gradient',(0.12,0.07,0.04))]:
        gradient=getattr(avatar,field)
        for position in (0,1):
            gradient.color_keys.add(key_position=position,red=color[0],green=color[1],blue=color[2])
    return avatar

def default_avatar(p,female=False):
    # References from the supplied client's onboarding presets ftue_0 / ftue_2.
    # The asset loader supplies its real textures, displacement maps and ramps.
    avatar=p.PlayerNeutralAvatarProto(neutral_avatar_legacy_mapping_version=1)
    articles={'hair':'n_hair_default_a_0' if female else 'n_hair_default_b_0',
        'shirt':'n_shirt_default-new_0','pants':'n_pants_default-new_0',
        'shoes':'n_shoes_default-new_0','backpack':'n_backpack_default-new_0',
        'gloves':'n_gloves_default-new_0','pose':'n_pose_idle',
        'face_preset':'FacePresetSix' if female else 'FacePresetSeven',
        'body_preset':'BodyPresetDefault' if female else 'BodyPresetFour',
        'eye_brow':'n_eyebrows_legacyfemale_0' if female else 'n_eyebrows_legacymale_0',
        'eye_lash':'n_eyelashes_legacyfemale_0' if female else 'n_eyelashes_legacymale_0'}
    for slot,article in articles.items():getattr(avatar.articles,slot).article_id=article
    avatar.head_selection.selection=7 if female else 8
    avatar.nose_selection.selection=avatar.mouth_selection.selection=1
    avatar.ear_selection.selection=avatar.eye_selection.selection=1
    for field in avatar.face_positions.DESCRIPTOR.fields:setattr(avatar.face_positions,field.name,0.5)
    if female:
        avatar.face_positions.brow_vertical=0.182;avatar.face_positions.eye_depth=0.35
        avatar.face_positions.eye_horizontal=0.4;avatar.face_positions.eye_vertical=0.249
        avatar.face_positions.mouth_vertical=0.553;avatar.face_positions.nose_vertical=0.447
    else:
        avatar.face_positions.brow_depth=0.626;avatar.face_positions.brow_vertical=0.18
        avatar.face_positions.eye_vertical=0.3;avatar.face_positions.nose_vertical=0.369
    avatar.body_blend.size=0 if female else 0.2
    avatar.body_blend.musculature=0 if female else 0.5
    avatar.body_blend.bust=0.6 if female else 0
    avatar.body_blend.hips=0.8 if female else 0.4
    avatar.body_blend.shoulders=0 if female else 0.6
    avatar.skin_gradient_id='n_gradient_skin_23'
    avatar.hair_gradient_id='n_gradient_hair_5'
    avatar.eye_gradient_id='n_gradient_eye_6'
    return avatar

def templates(p):
    template=p.GameMasterClientTemplateProto(template_id='NEUTRAL_AVATAR_SETTINGS')
    settings=template.neutral_avatar_settings
    settings.neutral_avatar_settings_enabled=True
    settings.neutral_avatar_settings_sentinel_value=1
    settings.neutral_avatar_legacy_mapping_version=1
    for field in ('default_neutral_avatar','male_neutral_avatar','female_neutral_avatar'):
        getattr(settings,field).CopyFrom(default_avatar(p))
    settings.female_neutral_avatar.CopyFrom(default_avatar(p,female=True))
    for field in settings.body_slider_settings.DESCRIPTOR.fields:
        getattr(settings.body_slider_settings,field.name).max_bounds=1
    return [template]
