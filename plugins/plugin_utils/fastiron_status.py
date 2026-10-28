# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parse FastIron "show cluster" output and check the live state of MCT clusters.

Shared by the glitchwrks.fastiron filter plugins. Runs on the controller.
"""

from __future__ import annotations

import re

_CLUSTER_RE = re.compile(r'^Cluster\s+(\S+)\s+(\d+)\s*$')
_RBRIDGE_RE = re.compile(r'(?<!Peer )Rbridge Id:\s*(\d+)')
_SESSION_VLAN_RE = re.compile(r'Session Vlan:\s*(\d+)')
_KEEP_ALIVE_VLAN_RE = re.compile(r'Keep-Alive Vlan:\s*(\d+)')
_STATE_RE = re.compile(r'^Cluster State:\s*(.*?)\s*$')
_ISOLATION_RE = re.compile(r'^Client Isolation Mode:\s*(.*?)\s*$')
_MEMBER_VLANS_RE = re.compile(r'^Member Vlan Range:\s*(.*?)\s*$')
_REACHABILITY_RE = re.compile(r'Reachability using Keep-Alive Vlan:\s*(.*?)\s*$')
_PEER_RE = re.compile(r'^Peer IP:\s*([^,\s]+)')
_PEER_RBRIDGE_RE = re.compile(r'Peer Rbridge Id:\s*(\d+)')
_PEER_ICL_RE = re.compile(r'ICL:\s*(\S+)')
_INTERVAL_RE = re.compile(r'KeepAlive Interval:\s*(\d+)')
_HOLD_TIME_RE = re.compile(r'Hold Time:\s*(\d+)')
_ACTIVE_VLANS_RE = re.compile(r'^Active Vlan Range:\s*(.*?)\s*$')
_CCP_DOWN_REASON_RE = re.compile(r'^Last Reason for CCP Down:\s*(.*?)\s*$')
_PEER_STATE_RE = re.compile(r'^Peer State:\s*([^(]*?)\s*(?:\((.*)\))?\s*$')
_CLIENT_COUNT_RE = re.compile(r'^Number of Clients configured:\s*(\d+)')
_ICL_ROW_RE = re.compile(r'^(\S+)\s+(\d+(?:/\d+)+)(?:\s+(\d+))?\s*$')
# Name, rbridge id, configuration state (which may be more than one word),
# port, LAG id (blank for a client on a single port) and FSM state.
_CLIENT_ROW_RE = re.compile(r'^(\S+)\s+(\d+)\s+(.+?)\s+(\d+(?:/\d+)+|N/A|None|-)\s+(?:(\d+)\s+)?(\S.*?)\s*$')

PEER_REACHABLE = 'Peer Reachable'
CLUSTER_DEPLOYED = 'Deploy'
CLIENT_DEPLOYED = 'Deployed'
CLIENT_UP = 'Up'
# Client states where the client is up on only one of the two switches.
CLIENT_HALF_UP = {
    'Remote Up': 'only up on the peer; its link to this switch is down',
    'Local Up': "only up on this switch; its link to the peer is down",
}


def _int(value):
    return int(value) if value is not None else None


def _vlans(text):
    return ' '.join(text.split())


def _new_cluster(name, cluster_id):
    return {'id': int(cluster_id), 'rbridge_id': None, 'session_vlan': None, 'keep_alive_vlan': None,
            'state': None, 'client_isolation': None, 'member_vlans': None, 'keep_alive_reachability': None,
            'icl': None, 'peers': [], 'clients_configured': None, 'clients': {}}


def parse_cluster_status(text):
    """Parse "show cluster" output into {'clusters': {name: {...}}}.

    Lines that aren't recognized are ignored; fields that weren't found are None.
    """
    clusters = {}
    cluster = None
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or set(line) <= set('=-'):
            continue
        m = _CLUSTER_RE.match(line)
        if m:
            cluster = clusters[m.group(1)] = _new_cluster(m.group(1), m.group(2))
            section = None
            continue
        if cluster is None:
            continue
        if line in ('ICL Info:', 'Peer Info:', 'Client Info:'):
            section = line[:-6].lower()
            continue

        if section is None:
            for regex, key, convert in ((_RBRIDGE_RE, 'rbridge_id', int), (_SESSION_VLAN_RE, 'session_vlan', int),
                                        (_KEEP_ALIVE_VLAN_RE, 'keep_alive_vlan', int)):
                m = regex.search(line)
                if m:
                    cluster[key] = convert(m.group(1))
            for regex, key in ((_STATE_RE, 'state'), (_ISOLATION_RE, 'client_isolation'),
                               (_REACHABILITY_RE, 'keep_alive_reachability')):
                m = regex.search(line)
                if m:
                    cluster[key] = m.group(1)
            m = _MEMBER_VLANS_RE.match(line)
            if m:
                cluster['member_vlans'] = _vlans(m.group(1))

        elif section == 'icl':
            m = _ICL_ROW_RE.match(line)
            if m and not line.startswith('Name '):
                cluster['icl'] = {'name': m.group(1), 'port': m.group(2), 'trunk': _int(m.group(3))}

        elif section == 'peer':
            m = _PEER_RE.match(line)
            if m:
                rbridge = _PEER_RBRIDGE_RE.search(line)
                icl = _PEER_ICL_RE.search(line)
                cluster['peers'].append({
                    'address': m.group(1), 'rbridge_id': _int(rbridge and rbridge.group(1)),
                    'icl': icl and icl.group(1), 'keep_alive_interval': None, 'hold_time': None,
                    'fast_failover': False, 'active_vlans': None, 'last_ccp_down_reason': None,
                    'state': None, 'state_detail': None})
                continue
            if not cluster['peers']:
                continue
            peer = cluster['peers'][-1]
            if line.startswith('KeepAlive Interval:'):
                interval, hold = _INTERVAL_RE.search(line), _HOLD_TIME_RE.search(line)
                peer['keep_alive_interval'] = _int(interval and interval.group(1))
                peer['hold_time'] = _int(hold and hold.group(1))
                peer['fast_failover'] = 'Fast Failover' in line
            for regex, key, convert in ((_ACTIVE_VLANS_RE, 'active_vlans', _vlans),
                                        (_CCP_DOWN_REASON_RE, 'last_ccp_down_reason', str)):
                m = regex.match(line)
                if m:
                    peer[key] = convert(m.group(1))
            m = _PEER_STATE_RE.match(line)
            if m:
                peer['state'] = m.group(1)
                peer['state_detail'] = ' '.join(m.group(2).split()) if m.group(2) else None

        elif section == 'client':
            m = _CLIENT_COUNT_RE.match(line)
            if m:
                cluster['clients_configured'] = int(m.group(1))
                continue
            if line.startswith('Name '):
                continue
            m = _CLIENT_ROW_RE.match(line)
            if m:
                cluster['clients'][m.group(1)] = {
                    'rbridge_id': int(m.group(2)), 'config': m.group(3), 'port': m.group(4),
                    'trunk': _int(m.group(5)), 'state': m.group(6)}

    return {'clusters': clusters}


def _finding(severity, rule, cluster, message):
    return {'severity': severity, 'rule': rule, 'cluster': cluster, 'message': message}


def mct_health(status, config=None):
    """Check parsed "show cluster" output for MCT clusters that aren't working.

    With a parsed running configuration, also check that each deployed cluster
    and client in it shows up in the status. Returns a list of findings.
    """
    findings = []
    clusters = status['clusters']

    if config is not None:
        for name, configured in sorted(config['clusters'].items()):
            if not configured['deployed']:
                continue
            if name not in clusters:
                findings.append(_finding('error', 'cluster_missing', name,
                                         'cluster %s is deployed in the configuration but not shown by "show cluster"'
                                         % name))
                continue
            for client_name, client in sorted(configured['clients'].items()):
                if client['deployed'] and client_name not in clusters[name]['clients']:
                    findings.append(_finding('error', 'client_missing', name,
                                             'client %s is deployed in the configuration but not shown by '
                                             '"show cluster"' % client_name))

    for name, cluster in sorted(clusters.items()):
        if cluster['state'] != CLUSTER_DEPLOYED:
            findings.append(_finding('error', 'cluster_not_deployed', name,
                                     'cluster %s is in state %s, not %s' % (name, cluster['state'], CLUSTER_DEPLOYED)))

        if cluster['keep_alive_vlan'] is not None:
            if cluster['keep_alive_reachability'] is None:
                findings.append(_finding('warning', 'keep_alive_unknown', name,
                                         'cluster %s: could not find the keep-alive reachability in "show cluster"'
                                         % name))
            elif cluster['keep_alive_reachability'] != PEER_REACHABLE:
                findings.append(_finding('error', 'keep_alive_unreachable', name,
                                         'cluster %s: peer not reachable over keep-alive VLAN %d (%s)'
                                         % (name, cluster['keep_alive_vlan'], cluster['keep_alive_reachability'])))

        if not cluster['peers']:
            findings.append(_finding('error', 'peer_missing', name, 'cluster %s shows no peer' % name))
        for peer in cluster['peers']:
            if not (peer['state'] or '').startswith('CCP Up'):
                findings.append(_finding('error', 'peer_down', name,
                                         'cluster %s: peer %s is in state %s; last reason for CCP down: %s'
                                         % (name, peer['address'], peer['state'], peer['last_ccp_down_reason'])))
            elif peer['active_vlans'] != cluster['member_vlans']:
                findings.append(_finding('warning', 'peer_vlans_inactive', name,
                                         'cluster %s: active VLANs with peer %s (%s) differ from the member VLANs (%s)'
                                         % (name, peer['address'], peer['active_vlans'], cluster['member_vlans'])))

        if cluster['clients_configured'] is not None and cluster['clients_configured'] != len(cluster['clients']):
            findings.append(_finding('warning', 'clients_unparsed', name,
                                     'cluster %s: "show cluster" reports %d client(s), but %d could be read'
                                     % (name, cluster['clients_configured'], len(cluster['clients']))))
        for client_name, client in sorted(cluster['clients'].items()):
            if client['config'] != CLIENT_DEPLOYED:
                findings.append(_finding('warning', 'client_not_deployed', name,
                                         'cluster %s: client %s is %s' % (name, client_name, client['config'])))
            elif client['state'] in CLIENT_HALF_UP:
                findings.append(_finding('error', 'client_down', name,
                                         'cluster %s: client %s is %s (%s)'
                                         % (name, client_name, CLIENT_HALF_UP[client['state']], client['state'])))
            elif client['state'] != CLIENT_UP:
                findings.append(_finding('error', 'client_down', name,
                                         'cluster %s: client %s is in state %s, not %s'
                                         % (name, client_name, client['state'], CLIENT_UP)))
    return findings
