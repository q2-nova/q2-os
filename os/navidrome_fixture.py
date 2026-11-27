"""Pinned real Navidrome, generated songs and credentials, isolated host ports."""
import contextlib
import http.client
import http.server
import json
import socket
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

IMAGE='sha256:3fa63c3ddc32d31c79970194d2d83143ac316b0bcef78695d1d9ab4c5a4f84b9'
USER='q2-fixture'
PASSWORD='synthetic-test-only'

class Fixture:
    def __init__(self, out, lifetime_seconds=None):
        if lifetime_seconds is not None and not 1 <= lifetime_seconds <= 3600:
            raise ValueError('fixture lifetime must be 1..3600 seconds')
        self.lifetime = [] if lifetime_seconds is None else ['--timeout', str(lifetime_seconds)]
        self.out=out;self.name='q2-nd-'+uuid.uuid4().hex[:12];self.network=self.name+'-net'
        self.servers=[];self.requests=[];self.offline=False;self.blackhole=False
    def api(self,endpoint,**params):
        query=urllib.parse.urlencode(dict(u=USER,p=PASSWORD,v='1.16.1',c='q2-test',f='json',**params))
        with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/rest/{endpoint}?{query}',timeout=10) as r:return json.load(r)['subsonic-response']
    def __enter__(self):
        self.out.mkdir(parents=True,exist_ok=True)
        music=self.out/'music';music.mkdir();data=self.out/'data';data.mkdir()
        for n in [1,2]:
            subprocess.run(['ffmpeg','-nostdin','-loglevel','error','-f','lavfi','-i',f'sine=frequency={440*n}:duration=120',
                '-ac','2','-ar','44100','-c:a','flac','-metadata','artist=Fixture Artist','-metadata','album=USB Album',
                '-metadata',f'title=USB tone {n}','-metadata',f'track={n}',str(music/f'tone-{n}.flac')],check=True)
        try:
            subprocess.run(['podman','network','create','--internal',self.network],check=True,stdout=subprocess.DEVNULL,timeout=30)
            subprocess.run(['podman','run','-d','--http-proxy=false',*self.lifetime,'--name',self.name,'--network',self.network,'--user','0:0',
                '-p','127.0.0.1::4533','--security-opt','label=disable','-v',f'{music}:/music:ro','-v',f'{data}:/data',
                '-e','ND_ENABLEINSIGHTS=false','-e','ND_ENABLEEXTERNALSERVICES=false','-e','ND_SCANSCHEDULE=0',IMAGE],check=True,stdout=subprocess.DEVNULL,timeout=90)
            port=subprocess.check_output(['podman','port',self.name,'4533/tcp'],text=True).strip()
            self.port=int(port.rsplit(':',1)[1])
            for _ in range(100):
                try:
                    req=urllib.request.Request(f'http://127.0.0.1:{self.port}/auth/createAdmin',data=json.dumps({'username':USER,'password':PASSWORD}).encode(),headers={'Content-Type':'application/json'})
                    with urllib.request.urlopen(req,timeout=2) as r:json.load(r)
                    break
                except (OSError,ValueError):time.sleep(.1)
            else:raise TimeoutError('Navidrome initialization')
            self.api('startScan')
            for _ in range(100):
                albums=self.api('getAlbumList2',type='alphabeticalByName').get('albumList2',{}).get('album',[])
                if albums:break
                time.sleep(.1)
            assert len(albums)==1,albums
            self.album=self.api('getAlbum',id=albums[0]['id'])['album']
            assert len(self.album['song'])==2,self.album
            self.http=self.proxy(False);self.https=self.proxy(True)
            (self.out/'fixture.json').write_text(json.dumps({'image':IMAGE,'album':self.album,'synthetic_credentials':True},indent=2)+'\n')
            return self
        except BaseException:
            self.__exit__(None,None,None);raise
    def proxy(self,tls):
        fixture=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                endpoint=urllib.parse.urlsplit(self.path).path
                fixture.requests.append(endpoint)
                if fixture.blackhole and (fixture.blackhole is True or endpoint.endswith('/stream')):
                    time.sleep(35)
                    self.close_connection=True;return
                if fixture.offline:self.send_error(503);return
                conn=http.client.HTTPConnection('127.0.0.1',fixture.port,timeout=10)
                try:
                    conn.request('GET',self.path,headers={k:v for k,v in self.headers.items() if k.lower() not in ['host','connection']})
                    r=conn.getresponse();self.send_response(r.status)
                    for k,v in r.getheaders():
                        if k.lower() not in ['connection','transfer-encoding']:self.send_header(k,v)
                    self.send_header('Connection','close');self.end_headers()
                    while chunk:=r.read(65536):self.wfile.write(chunk)
                except (OSError,http.client.HTTPException):pass
                finally:conn.close();self.close_connection=True
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        if tls:
            def openssl(*args):subprocess.run(['openssl',*args],cwd=self.out,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            openssl('req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-keyout','ca.key','-out','ca.pem','-days','2','-subj','/CN=Q2 generated fixture CA','-addext','basicConstraints=critical,CA:TRUE')
            openssl('req','-new','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-keyout','server.key','-out','server.csr','-subj','/CN=Q2 generated fixture server')
            (self.out/'extensions').write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:10.0.2.100\n')
            openssl('x509','-req','-in','server.csr','-CA','ca.pem','-CAkey','ca.key','-set_serial','2','-days','1','-extfile','extensions','-out','server.pem')
            openssl('x509','-in','ca.pem','-outform','DER','-out','ca.der')
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(self.out/'server.pem',self.out/'server.key')
            server.socket=context.wrap_socket(server.socket,server_side=True)
        self.servers.append(server);threading.Thread(target=server.serve_forever,daemon=True).start()
        return server.server_port
    def __exit__(self,*args):
        errors=[]
        for server in self.servers:
            try:server.shutdown();server.server_close()
            except Exception as error:errors.append('server close: '+type(error).__name__)
        try:
            with (self.out/'navidrome.log').open('w') as f:
                subprocess.run(['podman','logs',self.name],stdout=f,stderr=subprocess.STDOUT,timeout=30)
        except (OSError,subprocess.TimeoutExpired) as error:errors.append('log capture: '+type(error).__name__)
        # A log failure must not skip either owned-resource cleanup attempt.
        for command in [['podman','rm','-f',self.name],['podman','network','rm',self.network]]:
            try:subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
            except (OSError,subprocess.TimeoutExpired) as error:errors.append('resource removal: '+type(error).__name__)
        (self.out/'requests.json').write_text(json.dumps(self.requests,indent=2)+'\n')
        try:
            container_remains=subprocess.run(['podman','container','exists',self.name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10).returncode==0
            network_remains=subprocess.run(['podman','network','exists',self.network],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10).returncode==0
        except (OSError,subprocess.TimeoutExpired) as error:
            errors.append('cleanup verification: '+type(error).__name__);container_remains=network_remains=True
        complete=not container_remains and not network_remains
        (self.out/'cleanup.json').write_text(json.dumps({'complete':complete,'errors':errors},indent=2)+'\n')
        if not complete:raise RuntimeError('generated Navidrome cleanup failed')
