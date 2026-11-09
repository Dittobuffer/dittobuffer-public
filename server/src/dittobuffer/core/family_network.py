"""Explicit private listener/TLS configuration; never opens firewall or VPN routes."""
import ipaddress
import ssl
from pathlib import Path

def listener(config,root):
    host=config.get('host','127.0.0.1');family=config.get('authentication',{}).get('mode')=='family'
    try:local=ipaddress.ip_address(host).is_loopback
    except ValueError:local=host=='localhost'
    cfg=config.get('network',{});mode=cfg.get('mode','loopback')
    if mode not in ('loopback','tls','private-vpn'):raise ValueError('Unknown private network mode')
    if not family and not local:raise ValueError('Legacy identity server must remain on loopback')
    if not local and mode=='loopback':raise ValueError('Non-loopback family access requires explicit TLS or private VPN')
    if mode=='private-vpn':
        address=ipaddress.ip_address(host)
        allowed=[ipaddress.ip_network(v,strict=True) for v in cfg.get('allowed_networks',[])]
        private=[ipaddress.ip_network(v) for v in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10','fc00::/7')]
        if not allowed or not any(address in subnet for subnet in allowed) or not any(address in subnet for subnet in private):raise ValueError('Bind the explicit private VPN interface, not a public/wildcard listener')
        if not cfg.get('vpn_encryption_confirmed',False):raise ValueError('Private VPN encryption must be explicitly configured')
    context=None
    if mode=='tls':
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
        paths=[(root/cfg[key]).resolve() for key in ('certificate','private_key')]
        if any(not p.is_relative_to(root) or not p.is_file() for p in paths):raise ValueError('TLS files must exist inside the project; do not commit private keys')
        context.load_cert_chain(*map(str,paths))
    return context

def peer_allowed(config,peer):
    network=config.get('network',{})
    if network.get('mode')!='private-vpn' and 'allowed_networks' not in network:return True
    try:return any(ipaddress.ip_address(peer) in ipaddress.ip_network(v) for v in network.get('allowed_networks',[]))
    except ValueError:return False
