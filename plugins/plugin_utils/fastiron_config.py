# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse FastIron running configurations and audit Multi-Chassis Trunking (MCT).

Shared by the glitchwrks.fastiron filter plugins. Runs on the controller.
"""

from __future__ import annotations

import ipaddress
import re

_PORT_RE = re.compile(r'^\d+(?:/\d+)*$')


class FastIronConfigError(Exception):
    pass


def _blocks(text):
    """Split a configuration into (top-level line, [child lines]) pairs.

    Child lines keep their indentation relative to the block, so nested blocks
    (a cluster's client definitions) can be split again.
    """
    blocks = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip() == '!':
            continue
        if line[0] != ' ':
            blocks.append((line, []))
        elif blocks:
            blocks[-1][1].append(line[1:])
    return blocks


def _expand_range(start, end):
    a, b = start.split('/'), end.split('/')
    if len(a) != len(b) or a[:-1] != b[:-1]:
        raise FastIronConfigError('unsupported port range %s to %s' % (start, end))
    prefix = '/'.join(a[:-1])
    return ['%s/%d' % (prefix, n) if prefix else str(n) for n in range(int(a[-1]), int(b[-1]) + 1)]


def parse_ports(words):
    """Expand "ethe 1/1/2 ethe 1/1/7 to 1/1/8" style port lists into a list of ports."""
    ports = []
    i = 0
    while i < len(words):
        word = words[i]
        if word.startswith('ethe') or word == 'e':
            i += 1
            continue
        if _PORT_RE.match(word):
            if i + 2 < len(words) and words[i + 1] == 'to' and _PORT_RE.match(words[i + 2]):
                ports.extend(_expand_range(word, words[i + 2]))
                i += 3
                continue
            ports.append(word)
        i += 1
    seen = set()
    return [p for p in ports if not (p in seen or seen.add(p))]


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_config(text):
    """Parse a FastIron running configuration into a dictionary.

    Covers the parts needed to audit MCT: hostname, VLANs, LAGs, VE interface
    addresses and clusters, plus system-max settings and the port name,
    speed-duplex and dual-mode settings of Ethernet interfaces. Anything else
    is ignored.

    system_max maps the system-max parameters the configuration sets, such as
    vlan, to their values. A parameter left at its default is not listed: the
    running configuration doesn't show defaults.

    interfaces maps each port with an "interface ethernet" block to its name
    (port-name, or None), speed_duplex (or None) and dual_mode: False, True
    for dual-mode on the default VLAN, or the VLAN id given to dual-mode.
    """
    result = {'hostname': None, 'version': None, 'system_max': {}, 'vlans': {}, 'lags': {}, 've_addresses': {},
              'clusters': {}, 'interfaces': {}}

    for head, children in _blocks(text):
        words = head.split()
        if words[0] == 'hostname' and len(words) > 1:
            result['hostname'] = words[1]
        elif words[0] == 'ver' and len(words) > 1:
            result['version'] = words[1]
        elif words[0] == 'system-max' and len(words) > 2 and _int(words[2]) is not None:
            result['system_max'][words[1]] = int(words[2])
        elif words[0] == 'vlan' and len(words) > 1 and _int(words[1]) is not None:
            vlan = {'name': None, 'tagged': [], 'untagged': [], 'router_interface': None}
            name = re.search(r'\sname\s+("[^"]*"|\S+)', head)
            if name:
                vlan['name'] = name.group(1).strip('"')
            for child in children:
                cw = child.split()
                if cw[0] in ('tagged', 'untagged'):
                    vlan[cw[0]].extend(p for p in parse_ports(cw[1:]) if p not in vlan[cw[0]])
                elif cw[0] == 'router-interface' and len(cw) > 2:
                    vlan['router_interface'] = _int(cw[2])
            result['vlans'][int(words[1])] = vlan
        elif words[0] == 'lag' and len(words) > 1:
            m = re.match(r'lag\s+"?([^"\s]+)"?\s+(static|dynamic|keep-alive)?\s*(?:id\s+(\d+))?', head)
            lag = {'type': m.group(2) if m else None, 'id': _int(m.group(3)) if m else None,
                   'ports': [], 'primary_port': None, 'deployed': False}
            for child in children:
                cw = child.split()
                if cw[0] == 'ports':
                    lag['ports'].extend(parse_ports(cw[1:]))
                elif cw[0] == 'primary-port' and len(cw) > 1:
                    lag['primary_port'] = cw[1]
                elif cw[0] == 'deploy':
                    lag['deployed'] = True
            result['lags'][m.group(1) if m else words[1].strip('"')] = lag
        elif words[0] == 'interface' and len(words) > 2 and words[1].startswith('ethe'):
            for port in parse_ports(words[2:]):
                _parse_interface(result['interfaces'].setdefault(
                    port, {'name': None, 'speed_duplex': None, 'dual_mode': False}), children)
        elif words[0] == 'interface' and len(words) > 2 and words[1] == 've':
            for child in children:
                cw = child.split()
                if cw[:2] == ['ip', 'address'] and len(cw) > 2:
                    address = cw[2] if '/' in cw[2] or len(cw) < 4 else '%s/%s' % (cw[2], cw[3])
                    result['ve_addresses'].setdefault(_int(words[2]), []).append(address)
        elif words[0] == 'cluster' and len(words) > 1:
            result['clusters'][words[1]] = _parse_cluster(words, children)
    return result


def _parse_interface(interface, children):
    """Add the settings in an interface block to interface. A port's settings
    may be split over several blocks, as in a list of commands."""
    for child in children:
        cw = child.split()
        if cw[0] == 'port-name' and len(cw) > 1:
            interface['name'] = child.split(None, 1)[1].strip().strip('"')
        elif cw[0] == 'speed-duplex' and len(cw) > 1:
            interface['speed_duplex'] = cw[1]
        elif cw[0] == 'dual-mode':
            interface['dual_mode'] = _int(cw[1]) if len(cw) > 1 and _int(cw[1]) is not None else True


def _parse_cluster(words, children):
    cluster = {'id': _int(words[2]) if len(words) > 2 else None, 'rbridge_id': None, 'session_vlan': None,
               'keep_alive_vlan': None, 'icl': None, 'peers': [], 'deployed': False, 'clients': {}}
    for line, client_lines in _blocks('\n'.join(children)):
        cw = line.split()
        if cw[0] == 'rbridge-id':
            cluster['rbridge_id'] = _int(cw[1])
        elif cw[0] == 'session-vlan':
            cluster['session_vlan'] = _int(cw[1])
        elif cw[0] == 'keep-alive-vlan':
            cluster['keep_alive_vlan'] = _int(cw[1])
        elif cw[0] == 'icl' and len(cw) > 2:
            cluster['icl'] = {'name': cw[1], 'ports': parse_ports(cw[2:])}
        elif cw[0] == 'peer' and len(cw) > 1:
            peer = {'address': cw[1], 'rbridge_id': None, 'icl': None}
            if 'rbridge-id' in cw:
                peer['rbridge_id'] = _int(cw[cw.index('rbridge-id') + 1])
            if 'icl' in cw:
                peer['icl'] = cw[cw.index('icl') + 1]
            cluster['peers'].append(peer)
        elif cw[0] == 'deploy':
            cluster['deployed'] = True
        elif cw[0] == 'client' and len(cw) > 1:
            client = {'rbridge_id': None, 'interfaces': [], 'deployed': False}
            for client_line in client_lines:
                ccw = client_line.split()
                if ccw[0] == 'rbridge-id':
                    client['rbridge_id'] = _int(ccw[1])
                elif ccw[0] == 'client-interface':
                    client['interfaces'].extend(parse_ports(ccw[1:]))
                elif ccw[0] == 'deploy':
                    client['deployed'] = True
            cluster['clients'][cw[1]] = client
    return cluster


def _lag_ports(config, port):
    """All physical ports of the LAG containing port, or just the port."""
    for lag in config['lags'].values():
        if port in lag['ports']:
            return list(lag['ports'])
    return [port]


def icl_ports(config, cluster):
    ports = []
    for port in (cluster.get('icl') or {}).get('ports', []):
        ports.extend(p for p in _lag_ports(config, port) if p not in ports)
    return ports


def client_ports(config, client):
    ports = []
    for port in client['interfaces']:
        ports.extend(p for p in _lag_ports(config, port) if p not in ports)
    return ports


def _vlan_ports(vlan):
    return set(vlan['tagged']) | set(vlan['untagged'])


def _finding(severity, rule, cluster, message):
    return {'severity': severity, 'rule': rule, 'cluster': cluster, 'message': message}


def mct_audit(config):
    """Check one switch's MCT configuration. Returns a list of findings."""
    findings = []
    vlans = config['vlans']
    for name, cluster in sorted(config['clusters'].items()):
        icl = set(icl_ports(config, cluster))
        session, keep_alive = cluster['session_vlan'], cluster['keep_alive_vlan']

        if not cluster['icl']:
            findings.append(_finding('error', 'icl_missing', name, 'cluster has no ICL'))
        if not cluster['peers']:
            findings.append(_finding('error', 'peer_missing', name, 'cluster has no peer'))
        if not cluster['deployed']:
            findings.append(_finding('warning', 'cluster_not_deployed', name, 'cluster is not deployed'))
        for lag_name, lag in sorted(config['lags'].items()):
            if icl & set(lag['ports']) and not lag['deployed']:
                findings.append(_finding('error', 'icl_lag_not_deployed', name,
                                         'ICL LAG %s is not deployed' % lag_name))

        if session is None:
            findings.append(_finding('error', 'session_vlan_missing', name, 'no session VLAN configured'))
        elif session not in vlans:
            findings.append(_finding('error', 'session_vlan_undefined', name,
                                     'session VLAN %d is not defined' % session))
        else:
            missing = icl - set(vlans[session]['tagged'])
            if missing:
                findings.append(_finding('error', 'session_vlan_missing_icl', name,
                                         'session VLAN %d is not tagged on ICL port(s) %s'
                                         % (session, ', '.join(sorted(missing)))))
            extra = _vlan_ports(vlans[session]) - icl
            if extra:
                findings.append(_finding('warning', 'session_vlan_extra_ports', name,
                                         'session VLAN %d is also on non-ICL port(s) %s'
                                         % (session, ', '.join(sorted(extra)))))

        if keep_alive is None:
            findings.append(_finding('warning', 'keep_alive_vlan_missing', name,
                                     'no keep-alive VLAN configured; if the ICL fails, the peers cannot tell '
                                     'which one should keep each client'))
        elif keep_alive == session:
            findings.append(_finding('error', 'keep_alive_is_session_vlan', name,
                                     'keep-alive VLAN %d is the session VLAN' % keep_alive))
        elif keep_alive not in vlans:
            findings.append(_finding('error', 'keep_alive_vlan_undefined', name,
                                     'keep-alive VLAN %d is not defined' % keep_alive))
        else:
            ka_ports = _vlan_ports(vlans[keep_alive])
            shared = ka_ports & icl
            if shared:
                findings.append(_finding('error', 'keep_alive_on_icl', name,
                                         'keep-alive VLAN %d runs over ICL port(s) %s; it must use a separate '
                                         'link, or an ICL failure also takes out keep-alive'
                                         % (keep_alive, ', '.join(sorted(shared)))))
            if not ka_ports - icl:
                findings.append(_finding('error', 'keep_alive_no_separate_link', name,
                                         'keep-alive VLAN %d has no port outside the ICL' % keep_alive))

        for client_name, client in sorted(cluster['clients'].items()):
            if not client['deployed']:
                findings.append(_finding('warning', 'client_not_deployed', name,
                                         'client %s is not deployed' % client_name))
            if not client['interfaces']:
                findings.append(_finding('error', 'client_interface_missing', name,
                                         'client %s has no client interface' % client_name))
            cports = set(client_ports(config, client))
            for vlan_id, vlan in sorted(vlans.items()):
                if vlan_id in (session, keep_alive) or not (_vlan_ports(vlan) & cports):
                    continue
                if not icl <= _vlan_ports(vlan):
                    findings.append(_finding('error', 'client_vlan_missing_icl', name,
                                             'VLAN %d carries client %s but is not on the ICL'
                                             % (vlan_id, client_name)))
    return findings


def _client_vlans(config, client):
    cports = set(client_ports(config, client))
    return sorted(v for v, vlan in config['vlans'].items() if _vlan_ports(vlan) & cports)


def _owns_address(config, address):
    for addresses in config['ve_addresses'].values():
        for a in addresses:
            try:
                if ipaddress.ip_interface(a).ip == ipaddress.ip_address(address):
                    return True
            except ValueError:
                continue
    return False


def mct_compare(a, b, a_name='a', b_name='b'):
    """Check that two MCT peers' configurations agree. Returns a list of findings."""
    findings = []
    for name in sorted(set(a['clusters']) | set(b['clusters'])):
        ca, cb = a['clusters'].get(name), b['clusters'].get(name)
        if ca is None or cb is None:
            findings.append(_finding('error', 'cluster_missing_on_peer', name, 'cluster is only configured on %s'
                                     % (a_name if cb is None else b_name)))
            continue
        for key, label in (('id', 'cluster ID'), ('session_vlan', 'session VLAN'),
                           ('keep_alive_vlan', 'keep-alive VLAN')):
            if ca[key] != cb[key]:
                findings.append(_finding('error', '%s_mismatch' % key, name, '%s differs: %s on %s, %s on %s'
                                         % (label, ca[key], a_name, cb[key], b_name)))
        if ca['rbridge_id'] is not None and ca['rbridge_id'] == cb['rbridge_id']:
            findings.append(_finding('error', 'rbridge_id_duplicate', name,
                                     'both peers use rbridge-id %s' % ca['rbridge_id']))
        directions = ((ca, cb, b, a_name, b_name), (cb, ca, a, b_name, a_name))
        for this, other, other_cfg, this_name, other_name in directions:
            for peer in this['peers']:
                if peer['rbridge_id'] != other['rbridge_id']:
                    findings.append(_finding('error', 'peer_rbridge_id_mismatch', name,
                                             '%s expects peer rbridge-id %s, but %s uses %s'
                                             % (this_name, peer['rbridge_id'], other_name, other['rbridge_id'])))
                if not _owns_address(other_cfg, peer['address']):
                    findings.append(_finding('error', 'peer_address_mismatch', name,
                                             '%s expects its peer at %s, which is not an address on %s'
                                             % (this_name, peer['address'], other_name)))

        clients_a, clients_b = set(ca['clients']), set(cb['clients'])
        for client in sorted(clients_a ^ clients_b):
            findings.append(_finding('error', 'client_missing_on_peer', name, 'client %s is only configured on %s'
                                     % (client, a_name if client in clients_a else b_name)))
        for client in sorted(clients_a & clients_b):
            ka, kb = ca['clients'][client], cb['clients'][client]
            if ka['rbridge_id'] != kb['rbridge_id']:
                findings.append(_finding('error', 'client_rbridge_id_mismatch', name,
                                         'client %s rbridge-id differs: %s on %s, %s on %s'
                                         % (client, ka['rbridge_id'], a_name, kb['rbridge_id'], b_name)))
            va, vb = _client_vlans(a, ka), _client_vlans(b, kb)
            if va != vb:
                findings.append(_finding('error', 'client_vlans_mismatch', name,
                                         'client %s VLANs differ: %s on %s, %s on %s'
                                         % (client, va, a_name, vb, b_name)))
    return findings
