import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from dittobuffer.gameplay.game import Game,ROOT,p,wire,now_ms
from dittobuffer.core.database import Database

class ShopTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'builds')
        self.config=json.loads((ROOT/'config/development.json').read_text(encoding='utf8'))
        self.db=Database(Path(self.tmp.name)/'shop.sqlite3');self.game=Game(self.config,self.db);self.counter=0
        self.tickets=[self.game.authenticate(wire.AuthRequest(auth_id='shop-'+str(i))).ticket for i in range(2)]
        with self.db.transaction() as db:
            self.players=[db.execute('SELECT player_id FROM sessions WHERE ticket=?',(t,)).fetchone()[0] for t in self.tickets]
            db.execute('UPDATE trainer_teams SET coins=50')

    def tearDown(self):self.tmp.cleanup()

    def call(self,method,message,response,index=0,request=None):
        if request is None:
            self.counter+=1;request=wire.RpcRequest(rpc_id=self.counter)
            request.requests.add(method=method,body=message.SerializeToString())
        result=self.game.rpc(request,self.tickets[index])
        return response.FromString(result.payloads[0].payload)

    def available(self,index=0):return self.call(310001,p.GetAvailableSkusAndBalancesProto(),p.GetAvailableSkusAndBalancesOutProto,index)
    def buy(self,sku,index=0):return self.call(310000,p.PurchaseSkuProto(sku_id=sku.id,offer_id=sku.offer_id),p.PurchaseSkuOutProto,index)
    def state(self):
        with self.db.transaction() as db:
            return (db.execute('SELECT coins FROM trainer_teams WHERE player_id=?',(self.players[0],)).fetchone()[0],
                    db.execute('SELECT count FROM items WHERE player_id=? AND item=1',(self.players[0],)).fetchone()[0])

    def test_catalog_purchase_new_id_retry_restart_and_next_purchase(self):
        out=self.available();sku=out.available_sku[0];before=self.state()
        self.assertEqual((out.balance[0].quantity,sku.price[0].quantity,sku.game_item_content[0].type),(50,10,'ITEM_POKE_BALL'))
        self.assertFalse(sku.is_third_party_vendor_item)
        self.assertTrue(out.player_token)
        self.assertTrue(self.call(172,p.IsSkuAvailableProto(sku_id=sku.id,verify_price=True,coin_cost=10),p.IsSkuAvailableOutProto).is_sku_available)
        self.assertFalse(self.call(172,p.IsSkuAvailableProto(sku_id=sku.id,verify_price=True,coin_cost=11),p.IsSkuAvailableOutProto).is_sku_available)
        self.assertEqual(self.available().available_sku[0].offer_id,sku.offer_id)
        receipt=self.buy(sku);self.assertEqual(receipt.status,1)
        self.assertEqual((receipt.currency_update[0].currency_delta,receipt.currency_update[0].currency_balance),(-10,40))
        self.game=Game(self.config,Database(self.db.path))
        self.assertEqual(self.buy(sku),receipt)
        self.assertEqual(self.state(),(40,before[1]+20))
        another=self.available().available_sku[0];self.assertNotEqual(another.offer_id,sku.offer_id)
        self.assertEqual(self.buy(another).status,1);self.assertEqual(self.state(),(30,before[1]+40))

    def test_exact_transport_retry_and_owner_offer_validation(self):
        sku=self.available().available_sku[0];before=self.state()
        self.assertEqual(self.buy(sku,1).status,6)
        self.counter+=1;request=wire.RpcRequest(rpc_id=self.counter)
        request.requests.add(method=310000,body=p.PurchaseSkuProto(sku_id=sku.id,offer_id=sku.offer_id).SerializeToString())
        first=self.call(None,None,p.PurchaseSkuOutProto,request=request)
        second=self.call(None,None,p.PurchaseSkuOutProto,request=request)
        self.assertEqual(first,second);self.assertEqual(self.state(),(before[0]-10,before[1]+20))
        self.assertEqual(self.call(310000,p.PurchaseSkuProto(sku_id=sku.id),p.PurchaseSkuOutProto).status,6)

    def test_capacity_balance_expiry_and_price_change_are_atomic(self):
        sku=self.available().available_sku[0];before=self.state()
        self.game.config=self.config|{'item_capacity':1}
        self.assertEqual(self.buy(sku).status,5);self.assertEqual(self.state(),before)
        self.game.config=self.config
        with self.db.transaction() as db:db.execute('UPDATE trainer_teams SET coins=0 WHERE player_id=?',(self.players[0],))
        self.assertEqual(self.buy(sku).status,3)
        with self.db.transaction() as db:db.execute('UPDATE trainer_teams SET coins=50 WHERE player_id=?',(self.players[0],))
        with patch('dittobuffer.gameplay.shop.now_ms',return_value=now_ms()+3600000):self.assertEqual(self.buy(sku).status,6)
        self.game.config=self.config|{'shop':{'products':[{'sku':'pokeball.20','item':1,'quantity':20,'coins':11,'sprite':'pokeball.20'}]}}
        self.assertEqual(self.buy(sku).status,6);self.assertEqual(self.state(),before)

    def test_final_master_has_shop_dependencies_and_actual_wire_types(self):
        from dittobuffer.core.master import templates
        values={v.template_id:v for v in templates(self.config)}
        self.assertEqual(values['pokeball.20'].iap_item_display.category,2)
        self.assertTrue(values['RAID_SETTINGS'].HasField('raid_settings_proto'))
        self.assertFalse(values['RAID_SETTINGS'].raid_settings_proto.remote_raid_enabled)
        self.assertTrue(self.call(2,p.GetPlayerProto(),p.GetPlayerOutProto).player.HasField('team_change_info'))
        self.assertEqual(p.PurchaseSkuOutProto(added_inventory_item=[b'x']).SerializeToString(),b'\x12\x01x')
        self.assertEqual(p.GetAvailableSkusAndBalancesOutProto.DESCRIPTOR.fields_by_name['available_sku'].message_type.name,'AvailableSkuProto')
