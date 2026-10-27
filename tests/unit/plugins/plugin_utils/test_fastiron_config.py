# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os

import pytest

from ansible_collections.glitchwrks.fastiron.plugins.plugin_utils.fastiron_config import (
    FastIronConfigError,
    icl_ports,
    mct_audit,
    mct_compare,
    parse_config,
    parse_ports,
)

FIXTURES = os.path.join(os.path.dirname(__file__), '..', '..', 'fixtures')


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


@pytest.fixture
def cs1a_text():
    return fixture('cs1a_running_config.txt')


@pytest.fixture
def cs1b_text():
    return fixture('cs1b_running_config.txt')


def with_separate_keep_alive(text, port):
    """The lab configs run keep-alive over the ICL; move it to its own link."""
    return text.replace(
        'vlan 3001 name MCT-keep-alive by port\n tagged ethe 1/1/7 to 1/1/8\n',
        'vlan 3001 name MCT-keep-alive by port\n tagged ethe %s\n' % port)


def rules(findings):
    return sorted(f['rule'] for f in findings)


@pytest.mark.parametrize('words, expected', [
    (['ethe', '1/1/2'], ['1/1/2']),
    (['ethe', '1/1/2', 'ethe', '1/1/7', 'to', '1/1/8'], ['1/1/2', '1/1/7', '1/1/8']),
    (['ethernet', '1/2/1', 'to', '1/2/4'], ['1/2/1', '1/2/2', '1/2/3', '1/2/4']),
    (['ethe', '1/1/7', 'ethe', '1/1/7'], ['1/1/7']),
])
def test_parse_ports(words, expected):
    assert parse_ports(words) == expected


def test_parse_ports_rejects_cross_module_range():
    with pytest.raises(FastIronConfigError):
        parse_ports(['ethe', '1/1/7', 'to', '1/2/1'])


def test_parse_real_config(cs1a_text):
    config = parse_config(cs1a_text)
    assert config['hostname'] == 'cs1a.example.net'
    assert config['version'] == '08.0.30uT323'
    assert config['lags']['MCT_lag1'] == {'type': 'static', 'id': 2, 'ports': ['1/1/7', '1/1/8'],
                                          'primary_port': '1/1/7', 'deployed': True}
    assert config['vlans'][1000]['tagged'] == ['1/1/2', '1/1/7', '1/1/8']
    assert config['vlans'][3000]['router_interface'] == 3000
    assert config['ve_addresses'][3000] == ['10.0.1.2/255.255.255.0']
    cluster = config['clusters']['ALB-CORE']
    assert cluster['id'] == 4000
    assert cluster['rbridge_id'] == 2
    assert (cluster['session_vlan'], cluster['keep_alive_vlan']) == (3000, 3001)
    assert cluster['icl'] == {'name': 'ALB-CORE-MCT', 'ports': ['1/1/7']}
    assert cluster['peers'] == [{'address': '10.0.1.3', 'rbridge_id': 3, 'icl': 'ALB-CORE-MCT'}]
    assert cluster['deployed'] is True
    assert cluster['clients'] == {'c2960g': {'rbridge_id': 200, 'interfaces': ['1/1/2'], 'deployed': True}}


def test_icl_ports_expand_to_the_whole_lag(cs1a_text):
    config = parse_config(cs1a_text)
    assert icl_ports(config, config['clusters']['ALB-CORE']) == ['1/1/7', '1/1/8']


def test_audit_flags_keep_alive_over_the_icl(cs1a_text, cs1b_text):
    for text in (cs1a_text, cs1b_text):
        assert rules(mct_audit(parse_config(text))) == ['keep_alive_no_separate_link', 'keep_alive_on_icl']


def test_audit_passes_with_a_separate_keep_alive_link(cs1a_text):
    assert mct_audit(parse_config(with_separate_keep_alive(cs1a_text, '1/1/1'))) == []


@pytest.mark.parametrize('edit, expected', [
    # Client VLAN not carried over the ICL.
    (lambda t: t.replace('vlan 1000 by port\n tagged ethe 1/1/2 ethe 1/1/7 to 1/1/8',
                         'vlan 1000 by port\n tagged ethe 1/1/2'), ['client_vlan_missing_icl']),
    # Session VLAN missing from one ICL port.
    (lambda t: t.replace('vlan 3000 name Session-VLAN by port\n tagged ethe 1/1/7 to 1/1/8',
                         'vlan 3000 name Session-VLAN by port\n tagged ethe 1/1/7'), ['session_vlan_missing_icl']),
    # Session VLAN leaking onto a client port.
    (lambda t: t.replace('vlan 3000 name Session-VLAN by port\n tagged ethe 1/1/7 to 1/1/8',
                         'vlan 3000 name Session-VLAN by port\n tagged ethe 1/1/2 ethe 1/1/7 to 1/1/8'),
     ['session_vlan_extra_ports']),
    (lambda t: t.replace(' keep-alive-vlan 3001\n', ' keep-alive-vlan 3000\n'), ['keep_alive_is_session_vlan']),
    (lambda t: t.replace(' keep-alive-vlan 3001\n', ''), ['keep_alive_vlan_missing']),
    (lambda t: t.replace(' keep-alive-vlan 3001\n', ' keep-alive-vlan 3999\n'), ['keep_alive_vlan_undefined']),
    (lambda t: t.replace('  client-interface ethernet 1/1/2\n  deploy\n', '  client-interface ethernet 1/1/2\n'),
     ['client_not_deployed']),
    (lambda t: t.replace(' peer 10.0.1.3 rbridge-id 3 icl ALB-CORE-MCT\n deploy\n', ''),
     ['cluster_not_deployed', 'peer_missing']),
    (lambda t: t.replace(' primary-port 1/1/7\n deploy\n', ' primary-port 1/1/7\n'), ['icl_lag_not_deployed']),
])
def test_audit_rules(cs1a_text, edit, expected):
    text = edit(with_separate_keep_alive(cs1a_text, '1/1/1'))
    assert rules(mct_audit(parse_config(text))) == expected


def test_compare_real_pair_agrees(cs1a_text, cs1b_text):
    assert mct_compare(parse_config(cs1a_text), parse_config(cs1b_text), 'cs1a', 'cs1b') == []


@pytest.mark.parametrize('edit_b, expected', [
    (lambda t: t.replace('cluster ALB-CORE 4000', 'cluster ALB-CORE 4001'), ['id_mismatch']),
    (lambda t: t.replace(' session-vlan 3000\n', ' session-vlan 3002\n'), ['session_vlan_mismatch']),
    (lambda t: t.replace(' keep-alive-vlan 3001\n', ' keep-alive-vlan 3002\n'), ['keep_alive_vlan_mismatch']),
    # cs1b claims cs1a's rbridge-id: duplicate, and cs1a's peer entry no longer matches.
    (lambda t: t.replace(' rbridge-id 3\n', ' rbridge-id 2\n'),
     ['peer_rbridge_id_mismatch', 'rbridge_id_duplicate']),
    # cs1b points at an address cs1a doesn't have.
    (lambda t: t.replace('peer 10.0.1.2 ', 'peer 10.0.1.9 '), ['peer_address_mismatch']),
    (lambda t: t.replace('  rbridge-id 200\n', '  rbridge-id 201\n'), ['client_rbridge_id_mismatch']),
    (lambda t: t.replace('client c2960g', 'client c2960x'), ['client_missing_on_peer', 'client_missing_on_peer']),
    (lambda t: t.replace('vlan 1000 by port\n tagged ethe 1/1/2 ethe 1/1/7 to 1/1/8',
                         'vlan 1000 by port\n tagged ethe 1/1/7 to 1/1/8\n!\nvlan 1001 by port\n'
                         ' tagged ethe 1/1/2 ethe 1/1/7 to 1/1/8'),
     ['client_vlans_mismatch']),
    (lambda t: t[:t.index('cluster ALB-CORE')] + 'end\n', ['cluster_missing_on_peer']),
])
def test_compare_detects_mismatches(cs1a_text, cs1b_text, edit_b, expected):
    findings = mct_compare(parse_config(cs1a_text), parse_config(edit_b(cs1b_text)), 'cs1a', 'cs1b')
    assert rules(findings) == expected
    assert all(f['severity'] == 'error' for f in findings)
