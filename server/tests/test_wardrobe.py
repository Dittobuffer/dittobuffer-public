import json,tempfile,unittest
from pathlib import Path
from dittobuffer.gameplay.game import Game,ROOT,p,wire
from dittobuffer.core.database import Database


class WardrobeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds')
        self.config=json.loads((ROOT/'config/development.json').read_text())
        self.db=Database(Path(self.tmp.name)/'trainer.sqlite3');self.game=Game(self.config,self.db)
        self.ticket=self.game.authenticate(wire.AuthRequest(auth_id='wardrobe-test')).ticket;self.sequence=0
        with self.db.transaction() as db:self.player=db.execute('SELECT player_id FROM sessions WHERE ticket=?',(self.ticket,)).fetchone()[0]

    def tearDown(self):self.tmp.cleanup()

    def call(self,method,message,response):
        self.sequence+=1;request=wire.RpcRequest(rpc_id=self.sequence)
        request.requests.add(method=method,body=message.SerializeToString())
        return response.FromString(self.game.rpc(request,self.ticket).payloads[0].payload)

    def saved(self):
        with self.db.transaction() as db:return self.game.appearance(db,self.player)

    def test_native_list_save_restart_and_reject_unowned_wrong_slot(self):
        listing=self.call(409,p.ListAvatarStoreItemsProto(),p.ListAvatarStoreItemsOutProto)
        self.assertTrue(any(i.is_owned for v in listing.listings for i in v.items))
        for name,slot in [('SKIN',21),('EYE',22),('HAIR',23)]:
            palettes=[v for v in listing.listings if v.group_name=='CAT_'+name+'_COLOR_BAR']
            self.assertEqual(len(palettes),1,'Native initialization needs exactly one color-bar listing')
            self.assertTrue(palettes[0].items)
            self.assertTrue(all(i.slot==slot and i.article_id.startswith('n_gradient_'+name.lower()+'_') for i in palettes[0].items))
        avatar=self.saved();avatar.articles.hair.article_id='n_hair_wavyshort_0'
        result=self.call(408,p.SetNeutralAvatarProto(player_neutral_avatar_proto=avatar),p.SetNeutralAvatarOutProto)
        self.assertEqual(result.status,1)
        self.game=Game(self.config,Database(self.db.path));self.assertEqual(self.saved(),avatar)
        for field,value in [('hat','n_hat_strawhat_0'),('shoes','n_hair_wavyshort_0')]:
            bad=p.PlayerNeutralAvatarProto();bad.CopyFrom(avatar);getattr(bad.articles,field).article_id=value
            self.assertEqual(self.call(408,p.SetNeutralAvatarProto(player_neutral_avatar_proto=bad),p.SetNeutralAvatarOutProto).status,5)
            self.assertEqual(self.saved(),avatar)
        bad=p.PlayerNeutralAvatarProto();bad.CopyFrom(avatar);bad.articles.face_preset.article_id='FacePresetSix'
        self.assertEqual(self.call(408,p.SetNeutralAvatarProto(player_neutral_avatar_proto=bad),p.SetNeutralAvatarOutProto).status,5)
        bad=p.PlayerNeutralAvatarProto();bad.CopyFrom(avatar);bad.body_blend.size=float('nan')
        self.assertEqual(self.call(408,p.SetNeutralAvatarProto(player_neutral_avatar_proto=bad),p.SetNeutralAvatarOutProto).status,5)

    def test_existing_custom_appearance_is_preserved_and_owned(self):
        old=self.saved();old.articles.hair.article_id='n_hair_fallback_0'
        with self.db.transaction() as db:db.execute('UPDATE player_appearance SET data=? WHERE player_id=?',(old.SerializeToString(),self.player))
        result=self.call(4,p.GetHoloholoInventoryProto(),p.GetHoloholoInventoryOutProto)
        self.assertEqual(self.saved(),old)
        items=[p.HoloInventoryItemProto.FromString(v.item) for v in result.inventory_delta.inventory_item]
        self.assertTrue(any(v.neutral_avatar_item.neutral_avatar_article_template_id=='n_hair_fallback_0' for v in items))

    def test_avatar_asset_route_serves_only_manifest_checked_content(self):
        import hashlib
        from dittobuffer.core.asset_catalog import AssetStore
        from tests.test_asset_catalog import AssetCatalogTests
        manifest,document=AssetCatalogTests().complete_manifest()
        manifest['format']=1;manifest['route']='/assets/avatar'
        document['m_InternalIds'][0]=document['m_InternalIds'][0].replace('/assets/pokemon/','/assets/avatar/')
        raw=json.dumps(document).encode();root=Path(self.tmp.name)
        manifest.update(local_catalog='catalog.json',local_catalog_sha256=hashlib.sha256(raw).hexdigest())
        (root/'catalog.json').write_bytes(raw);(root/'fixture.bundle').write_bytes(b'fixture')
        (root/'manifest.json').write_text(json.dumps(manifest))
        store=AssetStore(root,{'enabled':True,'manifest':'manifest.json'})
        self.assertIsNone(store.read('/assets/pokemon/catalog.json'))
        self.assertIsNotNone(store.read('/assets/avatar/catalog.json'))
        self.assertEqual(store.read('/assets/avatar/catalog_avatars_88.json'),store.read('/assets/avatar/catalog.json'))
        self.assertIsNone(store.read('/assets/avatar/../../config/family.json'))

    def test_cosmetic_coin_purchase_retry_and_restart(self):
        with self.db.transaction() as db:
            from dittobuffer.gameplay.gyms import team
            team(db,self.player)
            db.execute('UPDATE trainer_teams SET coins=20 WHERE player_id=?',(self.player,))
        available=self.call(310001,p.GetAvailableSkusAndBalancesProto(),p.GetAvailableSkusAndBalancesOutProto)
        sku=next(v for v in available.available_sku if v.id=='dittobuffer.avatar.strawhat')
        request=p.PurchaseSkuProto(sku_id=sku.id,offer_id=sku.offer_id)
        receipt=self.call(310000,request,p.PurchaseSkuOutProto)
        self.assertEqual(receipt.status,1);self.assertEqual(receipt.currency_update[0].currency_balance,10)
        self.game=Game(self.config,Database(self.db.path))
        self.assertEqual(self.call(310000,request,p.PurchaseSkuOutProto),receipt)
        with self.db.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cosmetic_ownership WHERE player_id=? AND article_id=?',(self.player,'n_hat_strawhat_0')).fetchone()[0],1)
            self.assertEqual(db.execute('SELECT coins FROM trainer_teams WHERE player_id=?',(self.player,)).fetchone()[0],10)
        avatar=self.saved();avatar.articles.hat.article_id='n_hat_strawhat_0'
        self.assertEqual(self.call(408,p.SetNeutralAvatarProto(player_neutral_avatar_proto=avatar),p.SetNeutralAvatarOutProto).status,1)
        self.assertFalse(any(v.id==sku.id for v in self.call(310001,p.GetAvailableSkusAndBalancesProto(),p.GetAvailableSkusAndBalancesOutProto).available_sku))
        avatar.articles.hat.article_id='unequip'
        self.assertEqual(self.call(408,p.SetNeutralAvatarProto(player_neutral_avatar_proto=avatar),p.SetNeutralAvatarOutProto).status,1)
        self.assertFalse(self.saved().articles.hat.article_id)
        avatar=self.saved();avatar.articles.shirt.article_id='unequip'
        self.assertEqual(self.call(408,p.SetNeutralAvatarProto(player_neutral_avatar_proto=avatar),p.SetNeutralAvatarOutProto).status,5)

    def test_explicit_event_cosmetic_release_ownership_price_and_config_isolation(self):
        from dittobuffer.gameplay.wardrobe import catalog
        from dittobuffer.gameplay.shop import catalog as shop_catalog
        ident='n_shirt_svdlc1_0'
        self.config['wardrobe']={'offers':[dict(article_id=ident,coins=12,sku='dittobuffer.avatar.sv-shirt')]}
        self.assertEqual(shop_catalog(self.config),shop_catalog(self.config))
        before=self.saved();before.articles.shirt.article_id=ident
        self.assertEqual(self.call(408,p.SetNeutralAvatarProto(player_neutral_avatar_proto=before),p.SetNeutralAvatarOutProto).status,5)
        listing=self.call(409,p.ListAvatarStoreItemsProto(),p.ListAvatarStoreItemsOutProto)
        item=next(i for v in listing.listings for i in v.items if i.article_id==ident)
        self.assertTrue(item.is_purchasable);self.assertFalse(item.is_owned)
        with self.db.transaction() as db:db.execute('UPDATE trainer_teams SET coins=50 WHERE player_id=?',(self.player,))
        sku=next(v for v in self.call(310001,p.GetAvailableSkusAndBalancesProto(),p.GetAvailableSkusAndBalancesOutProto).available_sku if v.id=='dittobuffer.avatar.sv-shirt')
        request=p.PurchaseSkuProto(sku_id=sku.id,offer_id=sku.offer_id)
        result=self.call(310000,request,p.PurchaseSkuOutProto)
        self.assertEqual(result.currency_update[0].currency_balance,38)
        self.game=Game(self.config,Database(self.db.path))
        self.assertEqual(self.call(310000,request,p.PurchaseSkuOutProto),result)
        self.assertEqual(self.call(408,p.SetNeutralAvatarProto(player_neutral_avatar_proto=before),p.SetNeutralAvatarOutProto).status,1)
        self.config['wardrobe']={};self.assertEqual(self.saved(),before)
        self.assertEqual(next(v for v in catalog(self.config) if v['id']==ident)['ownership'],'event')
        for offer in (dict(article_id='n_shirt_default_a_0',coins=1,sku='dittobuffer.avatar.test'),dict(article_id=ident,coins=0,sku='dittobuffer.avatar.test'),dict(article_id=ident,coins=1,sku='production')):
            with self.subTest(offer=offer),self.assertRaises(ValueError):catalog({'wardrobe':{'offers':[offer]}})
