import argparse
import gzip
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
from google.protobuf.message import DecodeError
from dittobuffer.gameplay.game import Game, RpcError, ROOT, wire, p

def create_server(config='config/family.json', port=None):
    """Construct the real listener without starting its serving loop."""
    (ROOT/'logs').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s',handlers=[logging.FileHandler(ROOT/'logs/server.log',encoding='utf-8'),logging.StreamHandler()])
    config_path=(ROOT/config).resolve()
    if not config_path.is_relative_to(ROOT):raise ValueError('Configuration must stay inside the project')
    from dittobuffer.core.family_network import listener,peer_allowed
    settings=json.loads(config_path.read_text(encoding='utf-8'));tls=listener(settings,ROOT)
    game=Game(settings)
    from dittobuffer.world.world_map import configure
    configure(game.world,game.config)
    from dittobuffer.core.asset_catalog import AssetStore
    assets=AssetStore(ROOT,game.config.get('pokemon_assets',{}))
    avatar_assets=AssetStore(ROOT,game.config.get('avatar_assets',{}))
    from dittobuffer.core.client_resources import ClientResources
    client_resources=ClientResources(ROOT,game.config.get('client_resources',{}))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def send(self,status,body,content_type='application/x-protobuf'):
            self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)))
            if self.path.startswith('/accounts/') or self.path=='/get_auth_token':self.send_header('Cache-Control','no-store')
            self.end_headers();self.wfile.write(body)
        def send_battle(self,response):
            from dittobuffer.gameplay.game import now_ms
            body=response.SerializeToString()
            stamp=getattr(response,'time_ms',0) or getattr(response,'server_time_ms',0) or now_ms()
            self.send_response(200);self.send_header('Content-Type','application/x-protobuf')
            self.send_header('Content-Length',str(len(body)));self.send_header('nbl-gs-rsp-ts',str(stamp))
            self.end_headers();self.wfile.write(body)
        def client_header(self,suffix):
            current=self.headers.get('x-dittobuffer-'+suffix)
            legacy=self.headers.get('x-librenlp-'+suffix)
            if current is not None and legacy is not None and current!=legacy:
                # Values can contain session identities or location. Reject
                # ambiguity without disclosing either value in the log.
                raise ValueError('Conflicting client '+suffix+' header prefixes')
            return current if current is not None else legacy
        def cached_image(self,body,content_type):
            import hashlib
            etag='"'+hashlib.sha256(body).hexdigest()+'"'
            self.send_response(304 if self.headers.get('If-None-Match')==etag else 200)
            self.send_header('ETag',etag);self.send_header('Cache-Control','public, max-age=31536000, immutable')
            if self.headers.get('If-None-Match')==etag:self.end_headers();return
            self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def client_cdn_resource(self,name):
            """Fetch and cache a content-addressed runtime resource the device
            does not ship. The name is strictly validated and the official CDN
            the game itself uses is the only origin, so a registry gap can no
            longer leave UI art permanently blank."""
            import os,re,urllib.request
            name=name.strip('/')
            if '..' in name or not re.fullmatch(r'[A-Za-z0-9_./-]+\.(?:bundle|json|hash)',name):return None
            url='https://asset-cdn-rel.nianticstatic.com/'+name
            cache=ROOT/'data/client-cdn-cache'
            target=cache/name.replace('/','_')
            if target.is_file():
                body=target.read_bytes()
            else:
                try:
                    with urllib.request.urlopen(url,timeout=30) as response:
                        body=response.read(64*1024*1024)
                except Exception:
                    logging.warning('Client CDN fetch failed: %s',url)
                    return None
                cache.mkdir(parents=True,exist_ok=True)
                temporary=target.with_suffix(target.suffix+'.tmp')
                temporary.write_bytes(body);temporary.replace(target)
                logging.info('Client CDN resource cached: %s bytes=%d',name,len(body))
            return body,'application/json' if name.endswith('.json') else 'application/octet-stream'

        def do_GET(self):
            if not peer_allowed(game.config,self.client_address[0]):return self.send(403,b'')
            if self.path.split('?',1)[0]=='/assets/dittobuffer-research-card.png':
                from dittobuffer.gameplay.research_presentation import card_png
                return self.cached_image(card_png(),'image/png')
            if self.path.split('?',1)[0].split('=',1)[0]=='/assets/world-postcard.png':
                return self.cached_image((ROOT/'src/dittobuffer/resources/world-postcard.png').read_bytes(),'image/png')
            if self.path.startswith('/assets/client/'):
                name=self.path.removeprefix('/assets/client/').split('?',1)[0]
                resource=client_resources.read(name.rsplit('/',1)[-1])
                if resource is None:
                    resource=self.client_cdn_resource(name)
                if resource is None:
                    logging.warning('Runtime resource unavailable: %s',self.path.split('?',1)[0])
                    return self.send(404,b'')
                logging.info('Runtime resource served: %s bytes=%d',self.path.split('?',1)[0],len(resource[0]))
                return self.cached_image(*resource)
            if self.path=='/world/health':
                payload=dict(enabled=bool(game.world.path),epoch=game.world.epoch,
                    weather=bool(game.weather),
                    snapshot=game.world.manifest['database'] if game.world.manifest else None)
                self.send(200,json.dumps(payload).encode(),'application/json')
            elif self.path=='/world/photo-unavailable.png':
                from dittobuffer.world.world_images import placeholder
                self.cached_image(placeholder()['body'],'image/png')
            elif self.path.split('?',1)[0].startswith('/world/placeholders/'):
                from dittobuffer.world.world_images import placeholder
                image=placeholder();path=self.path.split('?',1)[0]
                if path!='/world/placeholders/'+image['sha256']+'.png':return self.send(404,b'')
                logging.info('Supplied geographic placeholder served: %s',image['sha256'][:12])
                self.cached_image(image['body'],'image/png')
            elif self.path.startswith('/world/photos/'):
                import re
                if not re.fullmatch(r'/world/photos/[a-f0-9]{64}\.jpg',self.path):return self.send(404,b'')
                if not game.world.photo_by_hash(self.path.rsplit('/',1)[1][:-4]):return self.send(404,b'')
                path=ROOT/'data/world/photos'/self.path.rsplit('/',1)[1]
                if not path.is_file():return self.send(404,b'')
                import hashlib
                raw=path.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=path.stem:return self.send(404,b'')
                logging.info('Verified geographic POI photo served: %s',path.name[:12])
                self.cached_image(raw,'image/jpeg')
            elif self.path.split('?',1)[0].startswith(('/assets/pokemon/','/assets/avatar/')):
                try:
                    resource=(avatar_assets if self.path.startswith('/assets/avatar/') else assets).read(self.path.split('?',1)[0])
                    if resource is None:
                        logging.warning('Local Pokemon asset unavailable: %s',self.path.split('?',1)[0])
                        return self.send(404,b'')
                    body,content_type=resource
                    import hashlib
                    etag='"'+hashlib.sha256(body).hexdigest()+'"'
                    if self.headers.get('If-None-Match')==etag:
                        self.send_response(304);self.send_header('ETag',etag);self.end_headers()
                        return
                    logging.info('Local Pokemon asset served path=%s bytes=%d',self.path.split('?',1)[0],len(body))
                    self.send_response(200);self.send_header('Content-Type',content_type)
                    self.send_header('Content-Length',str(len(body)));self.send_header('ETag',etag)
                    self.send_header('Cache-Control','public, max-age=31536000, immutable' if '/bundles/' in self.path else 'no-cache')
                    self.end_headers();self.wfile.write(body)
                except (OSError,ValueError):
                    logging.exception('Local Pokemon asset validation failed')
                    self.send(500,b'')
            elif self.path.split('?',1)[0] in ('/raid/api/get-state','/max/api/get-state'):
                maximum=self.path.split('?',1)[0].startswith('/max/')
                if maximum:
                    if not game.config.get('max_system',{}).get('enabled'):return self.send(403,b'')
                    from dittobuffer.gameplay.max_battle import handle
                else:
                    from dittobuffer.gameplay.raid_transport import handle
                import time
                time.sleep(.25)
                try:
                    response=handle(game,self.path.split('?',1)[0],self.headers)
                    self.send_battle(response)
                except RpcError as error:
                    logging.warning('%s state rejected: %s','Max' if maximum else 'Raid',error);self.send(403,b'')
                except Exception:
                    logging.exception('%s state failure','Max' if maximum else 'Raid');self.send(500,b'')
            elif self.path=='/map/settings':
                from dittobuffer.world.world_map import settings
                # The phone's map loader does not trust our private TLS chain,
                # so tiles are advertised on the plain-HTTP admin origin that
                # is already reversed to the device alongside this port.
                base=(game.config.get('world') or {}).get('map_tiles_base')
                payload=settings(base or None)
                logging.info('Local map composition requested: %d bytes base=%s',len(payload),base or 'game-port')
                self.send(200,payload)
            elif self.path.startswith('/map/tiles/'):
                from dittobuffer.world.world_map import tile
                from urllib.parse import urlparse,parse_qs
                try:
                    url=urlparse(self.path)
                    z,x,y=map(int,url.path.removeprefix('/map/tiles/').split('/'))
                    query=parse_qs(url.query)
                    payload=tile(z,x,y,int(query.get('epoch',['1'])[0]),int(query.get('tileZoom',[str(z)])[0]))
                    logging.info('Local vector tile served: %d bytes',len(payload))
                    self.send(200,payload)
                except ValueError as error:
                    logging.warning('Local vector tile rejected: %s (%s)',self.path,error)
                    self.send(400,b'')
            elif self.path.split('?',1)[0]=='/prelogin/settings.binpb.gz':
                logging.info('Local prelogin settings requested')
                self.send(200,gzip.compress(game.settings_bytes(),mtime=0),'application/gzip')
            elif self.path.split('?',1)[0]=='/prelogin/gmts.binpb.gz':
                from dittobuffer.core.master import prelogin
                logging.info('Local prelogin Game Master requested')
                self.send(200,prelogin(game.config),'application/gzip')
            elif self.path.split('?',1)[0] in ('/version','/plfe/version'):
                logging.info('Local version document requested')
                self.send(200,p.ClientVersionProto(min_version='0.429.2').SerializeToString())
            elif self.path=='/health':
                self.send(200,json.dumps({'status':'ok','target':'0.429.2','database':'sqlite',
                    'profile':game.config.get('profile','normal'),'pokemon_assets':assets.manifest is not None,
                    'authentication':'family' if game.accounts else 'local-development',
                    'avatar_assets':avatar_assets.manifest is not None,'client_resources':bool(client_resources.files),
                    'world':bool(game.world.path),
                    'client_catalog_sha256':client_resources.files.get('catalog_926.json',(None,None))[1]}).encode(),'application/json')
            elif self.path.split('?',1)[0].split('=',1)[0]=='/assets/dittobuffer-stop.png':
                logging.info('Local stop image requested')
                self.send(200,(ROOT/'src/dittobuffer/resources/dittobuffer-stop.png').read_bytes(),'image/png')
            elif self.path=='/assets/dittobuffer-original-background.bundle' and game.config.get('pokemon_backgrounds',{}).get('enabled',False):
                path=ROOT/'builds/dittobuffer-original-background.bundle'
                if path.exists():
                    logging.info('Local original background bundle requested')
                    self.send(200,path.read_bytes(),'application/octet-stream')
                else:self.send(404,b'')
            else:
                logging.info('Unknown local GET path=%s',self.path.split('?',1)[0])
                self.send(404,b'')
        def do_POST(self):
            try:
                if not peer_allowed(game.config,self.client_address[0]):return self.send(403,b'')
                size=int(self.headers.get('Content-Length','0'))
                if not 0<=size<=(8192 if self.path.startswith('/accounts/') else 8*1024*1024):
                    return self.send(413,b'')
                body=self.rfile.read(size)
                if self.path.startswith('/accounts/'):
                    if not game.accounts:return self.send(404,b'')
                    from dittobuffer.core.accounts import AccountError
                    try:
                        if size>8192:return self.send(413,b'')
                        value=json.loads(body)
                        if not isinstance(value,dict):raise AccountError('Invalid account request')
                        peer=self.client_address[0]
                        if self.path=='/accounts/register':
                            response=game.accounts.register(value.get('username'),value.get('password'),value.get('trainer_name'),value.get('invitation'),peer)
                        elif self.path=='/accounts/login':response=game.accounts.login(value.get('username'),value.get('password'),value.get('device_label',''),peer)
                        elif self.path=='/accounts/logout':
                            game.accounts.revoke(self.headers.get('Authorization','').removeprefix('Bearer '),value.get('all_sessions') is True);response={'logged_out':True}
                        else:return self.send(404,b'')
                        return self.send(200,json.dumps(response).encode(),'application/json')
                    except AccountError:
                        # Detailed errors may disclose account existence; no request/token logging.
                        return self.send(401,b'{"error":"Account request rejected"}','application/json')
                elif self.path.split('?',1)[0] in ('/raid/api/send-player-event','/raid/api/get-time','/raid/api/get-state'):
                    from dittobuffer.gameplay.raid_transport import handle
                    if self.path.split('?',1)[0].endswith('/get-state'):
                        import time
                        time.sleep(.25)
                    response=handle(game,self.path.split('?',1)[0],self.headers,body)
                elif self.path.split('?',1)[0] in ('/max/api/send-player-event','/max/api/get-time','/max/api/get-state'):
                    if not game.config.get('max_system',{}).get('enabled'):return self.send(403,b'')
                    from dittobuffer.gameplay.max_battle import handle
                    if self.path.split('?',1)[0].endswith('/get-state'):
                        import time
                        time.sleep(.25)
                    response=handle(game,self.path.split('?',1)[0],self.headers,body)
                    return self.send_battle(response)
                elif self.path=='/get_auth_token':
                    response=game.authenticate(wire.AuthRequest.FromString(body))
                    logging.info('Local session created')
                elif self.path=='/rpc':
                    self.client_header('rpc-id')
                    session=self.client_header('session') or ''
                    location={key:self.client_header(suffix) for key,suffix in [('lat','lat'),('lng','lng'),('accuracy','acc'),('time','fix-time'),('provider','provider'),('source','location-source')]}
                    request=wire.RpcRequest.FromString(body)
                    logging.info('RPC id=%d methods=%s',request.rpc_id,[(i.method,game.methods.get(i.method,'UNKNOWN'),len(i.body)) for i in request.requests])
                    response=game.rpc(request,self.headers.get('Authorization',''),session,location=location)
                    logging.info('RPC id=%d status=%d payload_sizes=%s',request.rpc_id,response.status,[len(i.payload) for i in response.payloads])
                elif self.path=='/map_query':
                    if game.accounts:
                        from dittobuffer.core.accounts import AccountError
                        with game.db.transaction() as db:
                            try:game.accounts.authorize(db,self.headers.get('Authorization',''))
                            except AccountError:return self.send(401,b'')
                    request=wire.MapQueryRequest.FromString(body)
                    logging.info('Map query received bytes=%d; callback delivery not implemented',len(request.body))
                    response=wire.MapQueryResponse(status=0)
                else:
                    logging.info('Unknown local POST path=%s bytes=%d',self.path.split('?',1)[0],len(body))
                    return self.send(404,b'')
                self.send(200,response.SerializeToString())
            except (RpcError,DecodeError,ValueError) as error:
                logging.warning('%s %s',self.path,error)
                self.send(400,b'')
            except Exception:
                logging.exception('RPC failure')
                self.send(500,b'')
    # Plain-HTTP map origin. The phone's Lightship map loader does not trust the
    # private game TLS chain, and the admin console may be closed, so the game
    # process serves map settings and vector tiles on their own loopback port.
    map_port=int((game.config.get('world') or {}).get('map_tiles_port',8081))
    class MapHandler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_GET(self):
            from urllib.parse import urlparse,parse_qs
            content_type='application/x-protobuf'
            if self.path.split('?',1)[0]=='/assets/dittobuffer-research-card.png':
                from dittobuffer.gameplay.research_presentation import card_png
                payload=card_png();content_type='image/png'
            elif self.path=='/map/settings':
                from dittobuffer.world.world_map import settings
                base=(game.config.get('world') or {}).get('map_tiles_base')
                payload=settings(base or None)
            elif self.path.startswith('/map/tiles/'):
                from dittobuffer.world.world_map import tile
                try:
                    url=urlparse(self.path)
                    z,x,y=map(int,url.path.removeprefix('/map/tiles/').split('/'))
                    query=parse_qs(url.query)
                    payload=tile(z,x,y,int(query.get('epoch',['1'])[0]),int(query.get('tileZoom',[str(z)])[0]))
                    logging.info('Plain-HTTP vector tile served: %d bytes path=%s',len(payload),self.path)
                except (ValueError,TypeError) as error:
                    logging.warning('Plain-HTTP vector tile rejected: %s (%s)',self.path,error)
                    self.send_response(400);self.end_headers();return
            else:
                self.send_response(404);self.end_headers();return
            self.send_response(200)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers();self.wfile.write(payload)
    class MapServer(ThreadingHTTPServer):
        request_queue_size = 64
        daemon_threads = True
    # POI importing always follows whoever is actually playing; the admin
    # console used to be the only starter, which left headless servers empty.
    try:
        from dittobuffer.world import poi_import
        def _playing_location():
            with game.db.transaction() as db:
                row = db.execute('SELECT lat,lng,seen_ms FROM admin_locations ORDER BY seen_ms DESC LIMIT 1').fetchone()
                if row:
                    return dict(row)
                row = db.execute('SELECT lat,lng,COALESCE(seen_ms,fix_ms,0) AS seen_ms FROM walking WHERE lat IS NOT NULL ORDER BY seen_ms DESC LIMIT 1').fetchone()
                return dict(row) if row else None
        poi_import.start(_playing_location)
    except Exception:
        logging.exception('Automatic OSM POI importing failed to start')
    # A platform room publishes a signed, lightweight JSON snapshot over the
    # HiveMQ bridge. Failures never block the local server.
    try:
        from dittobuffer.platform import client as platform_client
        platform_client.start(game, config_path)
    except Exception:
        logging.exception('Platform room publisher failed to start')
    address=(game.config['host'],game.config['port'] if port is None else port)
    logging.info('Listening on %s://%s:%s (%s)', 'https' if tls else 'http',*address,
                ('open family accounts' if game.config.get('authentication',{}).get('registration')=='open' else 'invite-only family accounts') if game.accounts else 'local test identities only')
    class Server(ThreadingHTTPServer):
        # The client opens dozens of parallel asset connections; the default
        # backlog of 5 drops TLS handshakes under that burst.
        request_queue_size = 128
        daemon_threads = True
        def server_close(self):
            maps=getattr(self,'map_server',None)
            if maps is not None:
                self.map_server=None
                maps.shutdown()
                maps.server_close()
            super().server_close()
    httpd=Server(address,Handler)
    if tls:httpd.socket=tls.wrap_socket(httpd.socket,server_side=True)
    httpd.map_server=None
    # The tile origin is independent of a published world snapshot. The phone
    # profile disables `world` because no snapshot is shipped, but roads still
    # come from data/maps and the OSM cache, so this listener must always come
    # up. A busy port is a warning, never a reason to fail the game server.
    try:
        maps=MapServer(('127.0.0.1',map_port),MapHandler)
    except OSError as error:
        logging.warning('Plain-HTTP map origin unavailable on 127.0.0.1:%s: %s',map_port,error)
    else:
        thread=threading.Thread(target=maps.serve_forever,name='map-origin',daemon=True)
        thread.start()
        httpd.map_server=maps
        logging.info('Map tiles on plain HTTP: http://127.0.0.1:%s/map/tiles',maps.server_address[1])
    from dittobuffer.world import osm_cache
    osm_cache.resume(game.config)
    return httpd


def serve(config='config/family.json', port=None):
    """Blocking entry point; only the listener created here is closed on exit."""
    with create_server(config,port) as httpd:
        httpd.serve_forever()


def main():
    parser=argparse.ArgumentParser(prog='dittobuffer-server',description='Run the Dittobuffer authoritative game server.')
    parser.add_argument('--port',type=int)
    parser.add_argument('--config',default='config/family.json')
    args=parser.parse_args()
    serve(args.config,args.port)


if __name__=='__main__':
    main()
