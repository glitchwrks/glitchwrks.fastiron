# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare FastIron running configurations with a declared model.

A model describes VLANs, LAGs, VE interface addresses and MCT clusters in the
same shape parse_config() returns, so a model can be bootstrapped from a
parsed running configuration, and the MCT checks work on models too. Only the
sections a model declares are compared; the others are left alone.

Changes are additive: commands are generated only for what can be added
safely (missing VLANs, ports, addresses, LAGs, clusters and clients, VLAN
names, deploying). Anything that needs removing something, or changing a LAG
or cluster in place, is reported as drift to fix by hand.

Runs on the controller.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping

from ansible.module_utils.common.collections import is_sequence

from ansible_collections.glitchwrks.fastiron.plugins.plugin_utils.fastiron_config import (
    FastIronConfigError,
    parse_config,
    parse_ports,
)

SECTIONS = ('vlans', 'lags', 've_addresses', 'clusters')
# What config_removals() can remove, when asked to. Nothing is removed by
# default.
REMOVABLE = ('vlan_ports',)
LAG_TYPES = ('static', 'dynamic')


# Validating a model


def _require_mapping(value, what):
    if not isinstance(value, Mapping):
        raise FastIronConfigError('%s must be a dictionary' % what)
    return value


def _check_keys(value, allowed, what):
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise FastIronConfigError('%s has unknown keys: %s' % (what, ', '.join(map(str, unknown))))


def _number(value, what):
    if isinstance(value, bool):
        raise FastIronConfigError('%s must be a number, got %r' % (what, value))
    try:
        return int(value)
    except (TypeError, ValueError):
        raise FastIronConfigError('%s must be a number, got %r' % (what, value))


def _ports(value, what):
    """A list of ports, or a string in switch syntax such as "ethe 1/1/7 to 1/1/8"."""
    if value is None:
        return []
    if isinstance(value, str):
        ports = parse_ports(value.split())
    elif is_sequence(value):
        ports = []
        for item in value:
            if not isinstance(item, str):
                raise FastIronConfigError('%s must list ports as strings, got %r' % (what, item))
            ports.extend(parse_ports(item.split()))
    else:
        raise FastIronConfigError('%s must be a list of ports' % what)
    if not ports and value:
        raise FastIronConfigError('%s does not name any ports: %r' % (what, value))
    seen = set()
    return [p for p in ports if not (p in seen or seen.add(p))]


def _bool(value, what):
    if not isinstance(value, bool):
        raise FastIronConfigError('%s must be true or false, got %r' % (what, value))
    return value


def _address(value, what):
    try:
        return ipaddress.ip_interface(str(value).replace(' ', '/'))
    except ValueError:
        raise FastIronConfigError('%s is not an address with a prefix length or netmask: %r' % (what, value))


def normalize_model(model):
    """Validate a model and return it in the shape parse_config() returns.

    Also returns the sections the model declares, which are the ones compared.
    """
    _require_mapping(model, 'the model')
    _check_keys(model, SECTIONS + ('hostname', 'version'), 'the model')
    result = {'hostname': model.get('hostname'), 'version': None,
              'vlans': {}, 'lags': {}, 've_addresses': {}, 'clusters': {}}
    managed = [s for s in SECTIONS if s in model]

    for key, vlan in _require_mapping(model.get('vlans') or {}, 'vlans').items():
        vid = _number(key, 'VLAN id')
        what = 'VLAN %d' % vid
        vlan = _require_mapping(vlan or {}, what)
        _check_keys(vlan, ('name', 'tagged', 'untagged', 'router_interface'), what)
        tagged = _ports(vlan.get('tagged'), what + ' tagged')
        untagged = _ports(vlan.get('untagged'), what + ' untagged')
        both = sorted(set(tagged) & set(untagged))
        if both:
            raise FastIronConfigError('%s has ports both tagged and untagged: %s' % (what, ', '.join(both)))
        name = vlan.get('name')
        if name is not None and (not isinstance(name, str) or not name or ' ' in name):
            raise FastIronConfigError('%s name must be a word without spaces, got %r' % (what, name))
        ve = vlan.get('router_interface')
        result['vlans'][vid] = {'name': name, 'tagged': tagged, 'untagged': untagged,
                                'router_interface': None if ve is None else _number(ve, what + ' router_interface')}

    for name, lag in _require_mapping(model.get('lags') or {}, 'lags').items():
        what = 'LAG %s' % name
        lag = _require_mapping(lag, what)
        _check_keys(lag, ('type', 'id', 'ports', 'primary_port', 'deployed'), what)
        if lag.get('type') not in LAG_TYPES:
            raise FastIronConfigError('%s type must be one of %s' % (what, ', '.join(LAG_TYPES)))
        ports = _ports(lag.get('ports'), what + ' ports')
        if not ports:
            raise FastIronConfigError('%s needs ports' % what)
        primary = lag.get('primary_port') or ports[0]
        if primary not in ports:
            raise FastIronConfigError('%s primary port %s is not one of its ports' % (what, primary))
        result['lags'][str(name)] = {'type': lag['type'], 'id': _number(lag.get('id'), what + ' id'),
                                     'ports': ports, 'primary_port': primary,
                                     'deployed': _bool(lag.get('deployed', True), what + ' deployed')}

    for key, addresses in _require_mapping(model.get('ve_addresses') or {}, 've_addresses').items():
        ve = _number(key, 'VE number')
        if isinstance(addresses, str) or not is_sequence(addresses):
            addresses = [addresses]
        result['ve_addresses'][ve] = [_address(a, 'VE %d address' % ve).with_netmask for a in addresses]

    for name, cluster in _require_mapping(model.get('clusters') or {}, 'clusters').items():
        what = 'cluster %s' % name
        cluster = _require_mapping(cluster, what)
        _check_keys(cluster, ('id', 'rbridge_id', 'session_vlan', 'keep_alive_vlan', 'icl', 'peers',
                              'deployed', 'clients'), what)
        icl = _require_mapping(cluster.get('icl') or {}, what + ' icl')
        _check_keys(icl, ('name', 'ports'), what + ' icl')
        if not icl.get('name') or not _ports(icl.get('ports'), what + ' icl ports'):
            raise FastIronConfigError('%s icl needs a name and ports' % what)
        peers = []
        for peer in cluster.get('peers') or []:
            peer = _require_mapping(peer, what + ' peer')
            _check_keys(peer, ('address', 'rbridge_id', 'icl'), what + ' peer')
            peers.append({'address': str(_address(peer.get('address'), what + ' peer address').ip),
                          'rbridge_id': _number(peer.get('rbridge_id'), what + ' peer rbridge_id'),
                          'icl': peer.get('icl') or icl['name']})
        clients = {}
        for cname, client in _require_mapping(cluster.get('clients') or {}, what + ' clients').items():
            cwhat = '%s client %s' % (what, cname)
            client = _require_mapping(client, cwhat)
            _check_keys(client, ('rbridge_id', 'interfaces', 'deployed'), cwhat)
            interfaces = _ports(client.get('interfaces'), cwhat + ' interfaces')
            if not interfaces:
                raise FastIronConfigError('%s needs interfaces' % cwhat)
            clients[str(cname)] = {'rbridge_id': _number(client.get('rbridge_id'), cwhat + ' rbridge_id'),
                                   'interfaces': interfaces,
                                   'deployed': _bool(client.get('deployed', True), cwhat + ' deployed')}
        result['clusters'][str(name)] = {
            'id': _number(cluster.get('id'), what + ' id'),
            'rbridge_id': _number(cluster.get('rbridge_id'), what + ' rbridge_id'),
            'session_vlan': _number(cluster.get('session_vlan'), what + ' session_vlan'),
            'keep_alive_vlan': (None if cluster.get('keep_alive_vlan') is None
                                else _number(cluster['keep_alive_vlan'], what + ' keep_alive_vlan')),
            'icl': {'name': icl['name'], 'ports': _ports(icl['ports'], what + ' icl ports')},
            'peers': peers,
            'deployed': _bool(cluster.get('deployed', True), what + ' deployed'),
            'clients': clients,
        }
    return result, managed


# Writing switch syntax


def _port_key(port):
    return tuple(int(p) for p in port.split('/'))


def format_ports(ports, keyword='ethe'):
    """Write ports the way FastIron shows them: "ethe 1/1/2 ethe 1/1/7 to 1/1/8"."""
    parts = []
    run = []
    for port in sorted(set(ports), key=_port_key):
        if run and _port_key(port)[:-1] == _port_key(run[-1])[:-1] and \
                _port_key(port)[-1] == _port_key(run[-1])[-1] + 1:
            run.append(port)
            continue
        if run:
            parts.append(run)
        run = [port]
    if run:
        parts.append(run)
    return ' '.join('%s %s' % (keyword, r[0]) if len(r) == 1 else '%s %s to %s' % (keyword, r[0], r[-1])
                    for r in parts)


def _netmask_form(address):
    interface = ipaddress.ip_interface(address.replace(' ', '/'))
    return '%s %s' % (interface.ip, interface.netmask)


# Comparing


def _finding(severity, rule, obj, message, fixable):
    return {'severity': severity, 'rule': rule, 'object': obj, 'message': message, 'fixable': fixable}


def _parsed(config):
    return config if isinstance(config, Mapping) else parse_config(config)


def _interfaces(addresses):
    return {ipaddress.ip_interface(a.replace(' ', '/')) for a in addresses}


def _removal_settings(current, want, remove, protected_vlans):
    """Validate the removal settings. Returns (set of removable kinds, set of protected VLAN ids).

    Each cluster's session VLAN, in the model or on the switch, is always
    protected: removing it from the ICL breaks the cluster's control link.
    """
    if remove is None:
        remove = []
    if not is_sequence(remove) or any(kind not in REMOVABLE for kind in remove):
        raise FastIronConfigError('remove must be a list of: %s' % ', '.join(REMOVABLE))
    if protected_vlans is None:
        protected_vlans = []
    if not is_sequence(protected_vlans):
        raise FastIronConfigError('protected_vlans must be a list of VLAN ids')
    protected = {_number(vid, 'protected VLAN') for vid in protected_vlans}
    for clusters in (current['clusters'], want.get('clusters', {})):
        for cluster in clusters.values():
            if cluster.get('session_vlan') is not None:
                protected.add(cluster['session_vlan'])
    return set(remove), protected


def config_drift(running, model, remove=None, protected_vlans=None):
    """Compare a running configuration (text or parsed) with a model.

    Returns findings: dictionaries with severity (error or warning), rule,
    object, message, and fixable (whether config_commands() or, for the kinds
    in remove, config_removals() fixes it).
    """
    current = _parsed(running)
    want, managed = normalize_model(model)
    removable, protected = _removal_settings(current, want, remove, protected_vlans)
    findings = []

    if 'vlans' in managed:
        for vid, vlan in sorted(want['vlans'].items()):
            obj = 'vlan %d' % vid
            have = current['vlans'].get(vid)
            if have is None:
                findings.append(_finding('error', 'vlan_missing', obj, 'VLAN %d is missing' % vid, True))
                continue
            if vlan['name'] is not None and have['name'] != vlan['name']:
                findings.append(_finding('error', 'vlan_name', obj, 'VLAN %d is named %s, should be %s'
                                         % (vid, have['name'], vlan['name']), True))
            for kind in ('tagged', 'untagged'):
                missing = sorted(set(vlan[kind]) - set(have[kind]), key=_port_key)
                extra = sorted(set(have[kind]) - set(vlan[kind]), key=_port_key)
                if missing:
                    findings.append(_finding('error', 'vlan_ports_missing', obj, 'VLAN %d is missing %s port(s) %s'
                                             % (vid, kind, ', '.join(missing)), True))
                if extra:
                    findings.append(_finding('error', 'vlan_ports_extra', obj, 'VLAN %d has extra %s port(s) %s%s'
                                             % (vid, kind, ', '.join(extra),
                                                ' (protected VLAN)' if vid in protected else ''),
                                             'vlan_ports' in removable and vid not in protected))
            if vlan['router_interface'] != have['router_interface']:
                fixable = have['router_interface'] is None
                findings.append(_finding('error', 'vlan_router_interface', obj,
                                         'VLAN %d router interface is ve %s, should be ve %s'
                                         % (vid, have['router_interface'], vlan['router_interface']), fixable))
        for vid in sorted(set(current['vlans']) - set(want['vlans'])):
            findings.append(_finding('warning', 'vlan_unmanaged', 'vlan %d' % vid,
                                     'VLAN %d is not in the model' % vid, False))

    if 've_addresses' in managed:
        for ve, addresses in sorted(want['ve_addresses'].items()):
            obj = 've %d' % ve
            have = _interfaces(current['ve_addresses'].get(ve, []))
            wanted = _interfaces(addresses)
            missing = sorted(str(a) for a in wanted - have)
            extra = sorted(str(a) for a in have - wanted)
            if missing:
                findings.append(_finding('error', 've_address_missing', obj, 've %d is missing address(es) %s'
                                         % (ve, ', '.join(missing)), True))
            if extra:
                findings.append(_finding('error', 've_address_extra', obj, 've %d has extra address(es) %s'
                                         % (ve, ', '.join(extra)), False))

    if 'lags' in managed:
        for name, lag in sorted(want['lags'].items()):
            obj = 'lag %s' % name
            have = current['lags'].get(name)
            if have is None:
                findings.append(_finding('error', 'lag_missing', obj, 'LAG %s is missing' % name, True))
                continue
            for key in ('type', 'id', 'primary_port'):
                if have[key] != lag[key]:
                    findings.append(_finding('error', 'lag_differs', obj, 'LAG %s %s is %s, should be %s'
                                             % (name, key.replace('_', ' '), have[key], lag[key]), False))
            if set(have['ports']) != set(lag['ports']):
                findings.append(_finding('error', 'lag_differs', obj, 'LAG %s ports are %s, should be %s'
                                         % (name, format_ports(have['ports'], 'ethernet'),
                                            format_ports(lag['ports'], 'ethernet')), False))
            if lag['deployed'] != have['deployed']:
                findings.append(_finding('error', 'lag_deployed', obj, 'LAG %s is %s' % (
                    name, 'not deployed' if lag['deployed'] else 'deployed, should not be'), lag['deployed']))
        for name in sorted(set(current['lags']) - set(want['lags'])):
            findings.append(_finding('warning', 'lag_unmanaged', 'lag %s' % name,
                                     'LAG %s is not in the model' % name, False))

    if 'clusters' in managed:
        for name, cluster in sorted(want['clusters'].items()):
            findings.extend(_cluster_drift(name, cluster, current['clusters'].get(name)))
        for name in sorted(set(current['clusters']) - set(want['clusters'])):
            findings.append(_finding('warning', 'cluster_unmanaged', 'cluster %s' % name,
                                     'cluster %s is not in the model' % name, False))
    return findings


def _cluster_drift(name, cluster, have):
    obj = 'cluster %s' % name
    if have is None:
        return [_finding('error', 'cluster_missing', obj, 'cluster %s is missing' % name, True)]
    findings = []
    for key in ('id', 'rbridge_id', 'session_vlan', 'keep_alive_vlan'):
        if have[key] != cluster[key]:
            findings.append(_finding('error', 'cluster_differs', obj, 'cluster %s %s is %s, should be %s'
                                     % (name, key.replace('_', ' '), have[key], cluster[key]), False))
    have_icl = have['icl'] or {'name': None, 'ports': []}
    if have_icl['name'] != cluster['icl']['name'] or set(have_icl['ports']) != set(cluster['icl']['ports']):
        findings.append(_finding('error', 'cluster_differs', obj, 'cluster %s ICL is %s %s, should be %s %s'
                                 % (name, have_icl['name'], format_ports(have_icl['ports'], 'ethernet'),
                                    cluster['icl']['name'], format_ports(cluster['icl']['ports'], 'ethernet')),
                                 False))
    if sorted(map(_peer_key, have['peers'])) != sorted(map(_peer_key, cluster['peers'])):
        findings.append(_finding('error', 'cluster_differs', obj, 'cluster %s peers are %s, should be %s' % (
            name, _peers_text(have['peers']), _peers_text(cluster['peers'])), False))
    if cluster['deployed'] != have['deployed']:
        findings.append(_finding('error', 'cluster_deployed', obj, 'cluster %s is %s' % (
            name, 'not deployed' if cluster['deployed'] else 'deployed, should not be'), cluster['deployed']))

    for cname, client in sorted(cluster['clients'].items()):
        cobj = '%s client %s' % (obj, cname)
        hc = have['clients'].get(cname)
        if hc is None:
            findings.append(_finding('error', 'client_missing', cobj, 'cluster %s client %s is missing'
                                     % (name, cname), True))
            continue
        if hc['rbridge_id'] != client['rbridge_id']:
            findings.append(_finding('error', 'client_differs', cobj, 'cluster %s client %s rbridge id is %s, '
                                     'should be %s' % (name, cname, hc['rbridge_id'], client['rbridge_id']), False))
        if set(hc['interfaces']) != set(client['interfaces']):
            findings.append(_finding('error', 'client_differs', cobj, 'cluster %s client %s interfaces are %s, '
                                     'should be %s' % (name, cname, format_ports(hc['interfaces'], 'ethernet'),
                                                       format_ports(client['interfaces'], 'ethernet')), False))
        if client['deployed'] != hc['deployed']:
            findings.append(_finding('error', 'client_deployed', cobj, 'cluster %s client %s is %s' % (
                name, cname, 'not deployed' if client['deployed'] else 'deployed, should not be'),
                client['deployed']))
    for cname in sorted(set(have['clients']) - set(cluster['clients'])):
        findings.append(_finding('warning', 'client_unmanaged', '%s client %s' % (obj, cname),
                                 'cluster %s client %s is not in the model' % (name, cname), False))
    return findings


def _peer_key(peer):
    return (peer['address'], peer['rbridge_id'], peer['icl'])


def _peers_text(peers):
    return ', '.join('%s rbridge-id %s' % (p['address'], p['rbridge_id']) for p in peers) or 'none'


# Generating commands


def _lag_header(name, lag):
    return 'lag "%s" %s id %d' % (name, lag['type'], lag['id'])


def _cluster_lines(cluster, with_clients):
    lines = [' rbridge-id %d' % cluster['rbridge_id'], ' session-vlan %d' % cluster['session_vlan']]
    if cluster['keep_alive_vlan'] is not None:
        lines.append(' keep-alive-vlan %d' % cluster['keep_alive_vlan'])
    lines.append(' icl %s %s' % (cluster['icl']['name'], format_ports(cluster['icl']['ports'], 'ethernet')))
    for peer in cluster['peers']:
        lines.append(' peer %s rbridge-id %d icl %s' % (peer['address'], peer['rbridge_id'], peer['icl']))
    if cluster['deployed']:
        lines.append(' deploy')
    if with_clients:
        for cname, client in sorted(cluster['clients'].items()):
            lines.extend(_client_lines(cname, client))
    return lines


def _client_lines(name, client):
    lines = [' client %s' % name, '  rbridge-id %d' % client['rbridge_id'],
             '  client-interface %s' % format_ports(client['interfaces'], 'ethernet')]
    if client['deployed']:
        lines.append('  deploy')
    return lines


def config_commands(running, model):
    """Commands that make a running configuration (text or parsed) match a model, as far as adding goes.

    Returns configuration lines, nested blocks indented by one space, in the
    order they can be applied: LAGs, VLANs, VE addresses, clusters. Drift that
    needs removing something or changing a LAG or cluster in place gets no
    commands; config_drift() reports it.
    """
    current = _parsed(running)
    want, managed = normalize_model(model)
    lines = []

    if 'lags' in managed:
        for name, lag in sorted(want['lags'].items()):
            have = current['lags'].get(name)
            if have is None:
                lines.append(_lag_header(name, lag))
                lines.append(' ports %s' % format_ports(lag['ports'], 'ethernet'))
                lines.append(' primary-port %s' % lag['primary_port'])
                if lag['deployed']:
                    lines.append(' deploy')
            elif lag['deployed'] and not have['deployed'] and \
                    (have['type'], have['id']) == (lag['type'], lag['id']):
                lines.extend([_lag_header(name, lag), ' deploy'])

    if 'vlans' in managed:
        for vid, vlan in sorted(want['vlans'].items()):
            have = current['vlans'].get(vid)
            body = []
            for kind in ('tagged', 'untagged'):
                missing = set(vlan[kind]) - set(have[kind] if have else [])
                if missing:
                    body.append(' %s %s' % (kind, format_ports(missing)))
            if vlan['router_interface'] is not None and (have is None or have['router_interface'] is None):
                body.append(' router-interface ve %d' % vlan['router_interface'])
            if have is None:
                header = 'vlan %d%s by port' % (vid, ' name %s' % vlan['name'] if vlan['name'] else '')
            elif vlan['name'] is not None and have['name'] != vlan['name']:
                header = 'vlan %d name %s' % (vid, vlan['name'])
            elif body:
                header = 'vlan %d' % vid
            else:
                continue
            lines.append(header)
            lines.extend(body)

    if 've_addresses' in managed:
        for ve, addresses in sorted(want['ve_addresses'].items()):
            have = _interfaces(current['ve_addresses'].get(ve, []))
            missing = [a for a in addresses if _interfaces([a]).pop() not in have]
            if missing:
                lines.append('interface ve %d' % ve)
                lines.extend(' ip address %s' % _netmask_form(a) for a in missing)

    if 'clusters' in managed:
        for name, cluster in sorted(want['clusters'].items()):
            have = current['clusters'].get(name)
            header = 'cluster %s %d' % (name, cluster['id'])
            if have is None:
                lines.append(header)
                lines.extend(_cluster_lines(cluster, with_clients=True))
                continue
            if have['id'] != cluster['id']:
                continue  # reported as drift; the header would name the wrong cluster
            body = []
            if cluster['deployed'] and not have['deployed']:
                body.append(' deploy')
            for cname, client in sorted(cluster['clients'].items()):
                hc = have['clients'].get(cname)
                if hc is None:
                    body.extend(_client_lines(cname, client))
                elif client['deployed'] and not hc['deployed'] and hc['rbridge_id'] == client['rbridge_id']:
                    body.extend([' client %s' % cname, '  deploy'])
            if body:
                lines.append(header)
                lines.extend(body)
    return lines


def config_removals(running, model, protected_vlans=None):
    """Return the configuration lines that remove ports from VLANs the model
    manages but doesn't list them in, nested by indenting one space.

    Only ports on VLANs in the model are removed, never VLANs themselves.
    Protected VLANs (protected_vlans, and each cluster's session VLAN) are left
    alone; config_drift() reports their extra ports as fixed by hand.
    """
    current = _parsed(running)
    want, managed = normalize_model(model)
    _removable, protected = _removal_settings(current, want, ['vlan_ports'], protected_vlans)
    lines = []
    if 'vlans' not in managed:
        return lines
    for vid, vlan in sorted(want['vlans'].items()):
        have = current['vlans'].get(vid)
        if have is None or vid in protected:
            continue
        body = []
        for kind in ('tagged', 'untagged'):
            extra = sorted(set(have[kind]) - set(vlan[kind]), key=_port_key)
            if extra:
                body.append(' no %s %s' % (kind, format_ports(extra)))
        if body:
            lines.append('vlan %d' % vid)
            lines.extend(body)
    return lines
