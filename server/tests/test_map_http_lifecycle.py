"""Real isolated map-listener lifecycle; no normal trainer or Android mutation."""
import json,socket,tempfile,unittest
from pathlib import Path
from urllib.request import urlopen
from dittobuffer.paths import ROOT
from dittobuffer.runtime.http import create_server

class MapHttpLifecycleTests(unittest.TestCase):
    def test_plain_map_listener_is_closed_with_game_service(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.private') as folder:
            folder=Path(folder)
            cfg=json.loads((ROOT/'config/development.json').read_text())
            cfg.update(host='127.0.0.1',port=0,database=(folder/'game.sqlite3').relative_to(ROOT).as_posix(),
                network={'mode':'loopback'},authentication={'mode':'local-development'},
                pokemon_assets={'enabled':False},avatar_assets={'enabled':False},client_resources={'enabled':False},
                world={'enabled':True,'json':'data/world/dev-world.json','osm_download':False,'osm_expand':False,'map_tiles_port':0})
            path=folder/'config.json';path.write_text(json.dumps(cfg))
            with create_server(path.relative_to(ROOT).as_posix(),0) as server:
                port=server.map_server.server_address[1]
                with urlopen(f'http://127.0.0.1:{port}/map/settings',timeout=2) as response:
                    self.assertEqual(response.status,200)
                    self.assertEqual(response.headers['Content-Type'],'application/x-protobuf')
                    self.assertTrue(response.read())
            with socket.socket() as client:
                client.settimeout(.5)
                self.assertNotEqual(client.connect_ex(('127.0.0.1',port)),0)
