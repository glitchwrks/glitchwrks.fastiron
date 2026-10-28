# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os

import pytest

from ansible_collections.glitchwrks.fastiron.plugins.plugin_utils.fastiron_config import parse_config
from ansible_collections.glitchwrks.fastiron.plugins.plugin_utils.fastiron_status import (
    mct_health,
    mct_health_compare,
    parse_cluster_status,
    parse_interface_brief,
)

FIXTURES = os.path.join(os.path.dirname(__file__), '..', '..', 'fixtures')


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


@pytest.fixture
def status_text():
    return fixture('cs1b_show_cluster.txt')


def rules(findings):
    return [(f['severity'], f['rule']) for f in findings]


def health(text, config=None):
    return mct_health(parse_cluster_status(text), config)


def test_parses_show_cluster(status_text):
    cluster = parse_cluster_status(status_text)['clusters']['ALB-CORE']
    assert cluster['id'] == 4000
    assert cluster['rbridge_id'] == 3
    assert cluster['session_vlan'] == 3000
    assert cluster['keep_alive_vlan'] == 3001
    assert cluster['state'] == 'Deploy'
    assert cluster['client_isolation'] == 'Loose'
    assert cluster['member_vlans'] == '1000 3001'
    assert cluster['keep_alive_reachability'] == 'Peer Reachable'
    assert cluster['icl'] == {'name': 'ALB-CORE-MCT', 'port': '1/1/7', 'trunk': 2}
    assert cluster['peers'] == [{
        'address': '10.0.1.2', 'rbridge_id': 2, 'icl': 'ALB-CORE-MCT', 'keep_alive_interval': 10,
        'hold_time': 90, 'fast_failover': True, 'active_vlans': '1000 3001',
        'last_ccp_down_reason': 'Not Down', 'ccp_down_reason': None, 'state': 'CCP Up',
        'state_detail': 'Up Time: 0 days:20 hr:12 min:53 sec'}]
    assert cluster['clients_configured'] == 1
    assert cluster['clients'] == {
        'c2960g': {'rbridge_id': 200, 'config': 'Deployed', 'port': '1/1/2', 'trunk': 104, 'state': 'Up'}}


def test_healthy_cluster_has_no_findings(status_text):
    assert health(status_text) == []


def test_healthy_cluster_matches_its_configuration(status_text):
    assert health(status_text, parse_config(fixture('cs1b_running_config.txt'))) == []


@pytest.mark.parametrize('old, new, expected', [
    ('Cluster State: Deploy', 'Cluster State: Undeploy', [('error', 'cluster_not_deployed')]),
    ('Keep-Alive Vlan: Peer Reachable', 'Keep-Alive Vlan: Peer Not Reachable', [('error', 'keep_alive_unreachable')]),
    ("MCT Peer's Reachability using Keep-Alive Vlan: Peer Reachable\n", '', [('warning', 'keep_alive_unknown')]),
    ('Peer State: CCP Up (Up Time:   0 days:20 hr:12 min:53 sec)', 'Peer State: CCP Down',
     [('error', 'peer_down')]),
    ('Active Vlan Range:  1000 3001', 'Active Vlan Range:  1000', [('warning', 'peer_vlans_inactive')]),
    ('Deployed    1/1/2    104  Up', 'Deployed    1/1/2    104  Down', [('error', 'client_down')]),
    ('Deployed    1/1/2    104  Up', 'Not Deployed 1/1/2    104  Down', [('warning', 'client_not_deployed')]),
    ('Number of Clients configured: 1', 'Number of Clients configured: 2', [('warning', 'clients_unparsed')]),
])
def test_problems_are_found(status_text, old, new, expected):
    assert old in status_text
    assert rules(health(status_text.replace(old, new))) == expected


def test_peer_down_names_the_last_reason(status_text):
    text = status_text.replace('Not Down', 'Keep-Alive Timeout').replace(
        'CCP Up (Up Time:   0 days:20 hr:12 min:53 sec)', 'CCP Down')
    [finding] = health(text)
    assert finding['message'] == 'cluster ALB-CORE: peer 10.0.1.2 is in state CCP Down; reason: Keep-Alive Timeout'


def test_peer_down_without_a_reason_yet(status_text):
    # For a few seconds after the session goes down, the last reason still reads Not Down.
    [finding] = health(status_text.replace('CCP Up (Up Time:   0 days:20 hr:12 min:53 sec)', 'CCP Down'))
    assert finding['message'] == 'cluster ALB-CORE: peer 10.0.1.2 is in state CCP Down'


def test_missing_peer(status_text):
    text = status_text.split(' Peer Info:')[0] + ' Client Info:' + status_text.split(' Client Info:')[1]
    assert rules(health(text)) == [('error', 'peer_missing')]


def test_client_on_a_single_port_has_no_trunk(status_text):
    text = status_text.replace('Deployed    1/1/2    104  Up', 'Deployed    1/1/2  Up')
    client = parse_cluster_status(text)['clusters']['ALB-CORE']['clients']['c2960g']
    assert (client['port'], client['trunk'], client['state']) == ('1/1/2', None, 'Up')
    assert health(text) == []


def test_configured_cluster_missing_from_status():
    findings = health('', parse_config(fixture('cs1b_running_config.txt')))
    assert rules(findings) == [('error', 'cluster_missing')]


def test_configured_client_missing_from_status(status_text):
    text = '\n'.join(line for line in status_text.splitlines() if not line.strip().startswith('c2960g'))
    text = text.replace('Number of Clients configured: 1', 'Number of Clients configured: 0')
    findings = health(text, parse_config(fixture('cs1b_running_config.txt')))
    assert rules(findings) == [('error', 'client_missing')]
    assert 'c2960g' in findings[0]['message']


def test_undeployed_parts_of_the_configuration_are_not_expected():
    config = parse_config(fixture('cs1b_running_config.txt'))
    config['clusters']['ALB-CORE']['deployed'] = False
    assert health('', config) == []


@pytest.mark.parametrize('name, side', [
    ('cs1a_show_cluster_client_remote_up.txt', 'only up on the peer; its link to this switch is down (Remote Up)'),
    ('cs1b_show_cluster_client_local_up.txt', 'only up on this switch; its link to the peer is down (Local Up)'),
])
def test_client_up_on_one_switch_only(name, side):
    # Captured with the client's link to cs1a unplugged.
    status = parse_cluster_status(fixture(name))
    assert status['clusters']['ALB-CORE']['peers'][0]['state'] == 'CCP Up'
    [finding] = mct_health(status)
    assert (finding['severity'], finding['rule']) == ('error', 'client_down')
    assert finding['message'] == 'cluster ALB-CORE: client c2960g is %s' % side


@pytest.mark.parametrize('name, peer', [('cs1a_show_cluster_icl_down.txt', '10.0.1.3'),
                                        ('cs1b_show_cluster_icl_down.txt', '10.0.1.2')])
def test_icl_down(name, peer):
    # Captured with both ICL links unplugged; this lab runs keep-alive over the ICL.
    status = parse_cluster_status(fixture(name))
    assert status['clusters']['ALB-CORE']['peers'][0]['ccp_down_reason'] == 'ICL interface down'
    assert [(f['rule'], f['message']) for f in mct_health(status)] == [
        ('keep_alive_unreachable',
         'cluster ALB-CORE: peer not reachable over keep-alive VLAN 3001 (Peer Unreachable)'),
        ('peer_down', 'cluster ALB-CORE: peer %s is in state CCP Down; reason: ICL interface down' % peer),
        ('client_down', 'cluster ALB-CORE: client c2960g is Master on this switch: with the CCP session down, '
                        'this switch serves it on its own'),
    ]


@pytest.mark.parametrize('name', ['cs1a_show_cluster_keep_alive_down.txt', 'cs1b_show_cluster_keep_alive_down.txt'])
def test_keep_alive_link_down(name):
    # Captured with the keep-alive link (on its own port, not the ICL) unplugged. cs1a still shows
    # the reason for an earlier CCP outage, which doesn't matter while CCP is up.
    findings = mct_health(parse_cluster_status(fixture(name)))
    assert [(f['severity'], f['rule'], f['message']) for f in findings] == [
        ('error', 'keep_alive_unreachable',
         'cluster ALB-CORE: peer not reachable over keep-alive VLAN 3001 (Peer Unreachable)')]


def test_split_brain_when_both_switches_are_master():
    a = parse_cluster_status(fixture('cs1a_show_cluster_icl_down.txt'))
    b = parse_cluster_status(fixture('cs1b_show_cluster_icl_down.txt'))
    assert mct_health_compare(a, b, 'cs1a', 'cs1b') == [{
        'severity': 'error', 'rule': 'split_brain', 'cluster': 'ALB-CORE',
        'message': 'cluster ALB-CORE: client c2960g is Master on both cs1a and cs1b (split-brain): '
                   'both switches serve it on their own'}]


@pytest.mark.parametrize('a, b', [
    ('cs1b_show_cluster.txt', 'cs1b_show_cluster.txt'),
    ('cs1a_show_cluster_client_remote_up.txt', 'cs1b_show_cluster_client_local_up.txt'),
    ('cs1a_show_cluster_keep_alive_down.txt', 'cs1b_show_cluster_keep_alive_down.txt'),
])
def test_no_split_brain(a, b):
    assert mct_health_compare(parse_cluster_status(fixture(a)), parse_cluster_status(fixture(b))) == []


def test_parses_show_interface_brief():
    ports = parse_interface_brief(fixture('cs1a_show_interface_brief_icl_link_down.txt'))
    assert ports == {
        '1/1/7': {'link': 'Up', 'state': 'Forward', 'duplex': 'Full', 'speed': '10G', 'trunk': '2', 'tag': 'Yes',
                  'pvid': 'N/A', 'priority': '0', 'mac': '0012.3456.789a', 'name': None},
        '1/1/8': {'link': 'Down', 'state': 'None', 'duplex': 'None', 'speed': 'None', 'trunk': '2', 'tag': 'Yes',
                  'pvid': 'N/A', 'priority': '0', 'mac': '0012.3456.789a', 'name': None},
    }


@pytest.fixture
def cs1a_config():
    return parse_config(fixture('cs1a_running_config.txt'))


def interfaces(links):
    return {port: {'link': link} for port, link in links.items()}


def test_icl_link_down(cs1a_config):
    # Captured with one ICL link's optic failed: show cluster still shows the cluster healthy.
    ports = parse_interface_brief(fixture('cs1a_show_interface_brief_icl_link_down.txt'))
    findings = mct_health({'clusters': {}}, cs1a_config, ports)
    assert [(f['severity'], f['rule'], f['message']) for f in findings if f['rule'].startswith('icl')] == [
        ('error', 'icl_port_down', 'cluster ALB-CORE: ICL port 1/1/8 is down (Down); the ICL has lost redundancy')]


def test_whole_icl_down(cs1a_config):
    findings = mct_health({'clusters': {}}, cs1a_config, interfaces({'1/1/7': 'Down', '1/1/8': 'Down'}))
    assert [f['message'] for f in findings if f['rule'] == 'icl_port_down'] == [
        'cluster ALB-CORE: ICL port 1/1/7 is down (Down); the ICL is down',
        'cluster ALB-CORE: ICL port 1/1/8 is down (Down); the ICL is down']


def test_icl_links_up(status_text, cs1a_config):
    findings = health(status_text, cs1a_config)
    assert findings == []
    assert mct_health(parse_cluster_status(status_text), cs1a_config,
                      interfaces({'1/1/7': 'Up', '1/1/8': 'Up'})) == []


def test_icl_port_missing_from_the_interfaces(cs1a_config):
    findings = mct_health({'clusters': {}}, cs1a_config, interfaces({'1/1/7': 'Up'}))
    assert [(f['severity'], f['rule']) for f in findings if f['rule'].startswith('icl')] == [
        ('warning', 'icl_port_unknown')]
