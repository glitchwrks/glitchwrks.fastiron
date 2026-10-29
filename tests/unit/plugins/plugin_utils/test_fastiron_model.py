# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import copy
import os

import pytest

from ansible_collections.glitchwrks.fastiron.plugins.plugin_utils.fastiron_config import (
    FastIronConfigError,
    mct_audit,
    mct_compare,
    parse_config,
)
from ansible_collections.glitchwrks.fastiron.plugins.plugin_utils.fastiron_model import (
    config_commands,
    clusters_to_undeploy,
    config_drift,
    config_removals,
    format_ports,
    normalize_model,
)

FIXTURES = os.path.join(os.path.dirname(__file__), '..', '..', 'fixtures')
EMPTY = 'Current configuration:\n!\nver 08.0.30uT323\n!\nend\n'


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


def model_of(text):
    """A model taken from a running configuration, as a user would bootstrap one."""
    parsed = parse_config(text)
    return copy.deepcopy({key: parsed[key] for key in ('vlans', 'lags', 've_addresses', 'clusters')})


@pytest.fixture
def cs1a():
    return fixture('cs1a_running_config.txt')


@pytest.fixture
def model(cs1a):
    return model_of(cs1a)


def rules(findings):
    return sorted((f['rule'], f['fixable']) for f in findings)


@pytest.mark.parametrize('ports, keyword, expected', [
    (['1/1/2'], 'ethe', 'ethe 1/1/2'),
    (['1/1/8', '1/1/2', '1/1/7'], 'ethe', 'ethe 1/1/2 ethe 1/1/7 to 1/1/8'),
    (['1/1/7', '1/1/8', '1/2/1', '1/2/2', '1/2/4'], 'ethernet', 'ethernet 1/1/7 to 1/1/8 ethernet 1/2/1 to 1/2/2 ethernet 1/2/4'),
    (['1/1/10', '1/1/9'], 'ethe', 'ethe 1/1/9 to 1/1/10'),
])
def test_format_ports(ports, keyword, expected):
    assert format_ports(ports, keyword) == expected


def test_running_config_is_a_model_of_itself(cs1a, model):
    assert config_drift(cs1a, model) == []
    assert config_commands(cs1a, model) == []


def test_commands_written_as_the_switch_shows_them(model):
    # Reading the generated commands back as a running configuration gives the model again.
    commands = config_commands(EMPTY, model)
    rebuilt = 'Current configuration:\n!\n%s\nend\n' % '\n'.join(commands)
    assert config_drift(rebuilt, model) == []
    assert commands[:4] == ['lag "MCT_lag1" static id 2', ' ports ethernet 1/1/7 to 1/1/8',
                            ' primary-port 1/1/7', ' deploy']
    assert ' peer 10.0.1.3 rbridge-id 3 icl ALB-CORE-MCT' in commands


def test_model_shorthand():
    model = {
        'vlans': {'1000': {'tagged': 'ethe 1/1/7 to 1/1/8', 'untagged': ['1/1/2']}},
        'lags': {'icl': {'type': 'static', 'id': 2, 'ports': ['1/1/7', '1/1/8']}},
        've_addresses': {3000: '10.0.1.2/24'},
        'clusters': {'C': {'id': 1, 'rbridge_id': 2, 'session_vlan': 3000,
                           'icl': {'name': 'I', 'ports': ['1/1/7']},
                           'peers': [{'address': '10.0.1.3', 'rbridge_id': 3}],
                           'clients': {'sw': {'rbridge_id': 200, 'interfaces': '1/1/2'}}}},
    }
    config, managed = normalize_model(model)
    assert managed == ['vlans', 'lags', 've_addresses', 'clusters']
    assert config['vlans'][1000] == {'name': None, 'tagged': ['1/1/7', '1/1/8'], 'untagged': ['1/1/2'],
                                     'router_interface': None}
    assert config['lags']['icl']['primary_port'] == '1/1/7'
    assert config['lags']['icl']['deployed'] is True
    assert config['ve_addresses'][3000] == ['10.0.1.2/255.255.255.0']
    assert config['clusters']['C']['peers'] == [{'address': '10.0.1.3', 'rbridge_id': 3, 'icl': 'I'}]
    assert config['clusters']['C']['keep_alive_vlan'] is None
    assert config['clusters']['C']['clients']['sw']['deployed'] is True


@pytest.mark.parametrize('model, message', [
    ({'vlanz': {}}, 'unknown keys: vlanz'),
    ({'vlans': {'x': {}}}, 'VLAN id must be a number'),
    ({'vlans': {10: {'colour': 'red'}}}, 'VLAN 10 has unknown keys: colour'),
    ({'vlans': {10: {'tagged': ['1/1/1'], 'untagged': ['1/1/1']}}}, 'both tagged and untagged: 1/1/1'),
    ({'vlans': {10: {'name': 'two words'}}}, 'name must be a word'),
    ({'vlans': {10: {'tagged': [7]}}}, 'list ports as strings'),
    ({'lags': {'l': {'type': 'lacp', 'id': 1, 'ports': ['1/1/1']}}}, 'type must be one of'),
    ({'lags': {'l': {'type': 'static', 'id': 1, 'ports': ['1/1/1'], 'primary_port': '1/1/2'}}},
     'primary port 1/1/2 is not one of its ports'),
    ({'lags': {'l': {'type': 'static', 'id': 1, 'ports': ['1/1/1'], 'deployed': 'yes'}}}, 'true or false'),
    ({'ve_addresses': {1: 'nonsense'}}, 'not an address'),
    ({'clusters': {'C': {'id': 1, 'rbridge_id': 2, 'session_vlan': 3}}}, 'icl needs a name and ports'),
])
def test_invalid_models(model, message):
    with pytest.raises(FastIronConfigError, match=message):
        normalize_model(model)


def test_sections_not_in_the_model_are_not_compared(cs1a):
    assert config_drift(EMPTY, {'vlans': {}}) == []
    assert config_commands(EMPTY, {}) == []


@pytest.mark.parametrize('edit, expected', [
    (lambda m: m['vlans'].update({2000: {'name': 'Lab', 'tagged': ['1/1/7', '1/1/8'], 'untagged': [],
                                         'router_interface': None}}),
     [('vlan_missing', True)]),
    (lambda m: m['vlans'][1000]['tagged'].append('1/1/3'), [('vlan_ports_missing', True)]),
    (lambda m: m['vlans'][1000]['tagged'].remove('1/1/2'), [('vlan_ports_extra', False)]),
    (lambda m: m['vlans'][3000].update(name='Session'), [('vlan_name', True)]),
    (lambda m: m['vlans'][1000].update(router_interface=1000), [('vlan_router_interface', True)]),
    (lambda m: m['vlans'][3000].update(router_interface=3999), [('vlan_router_interface', False)]),
    (lambda m: m['vlans'].pop(3001), [('vlan_unmanaged', False)]),
    (lambda m: m['ve_addresses'].update({3000: ['10.0.1.9/24']}),
     [('ve_address_extra', False), ('ve_address_missing', True)]),
    (lambda m: m['ve_addresses'][3000].append('10.0.2.2/24'), [('ve_address_missing', True)]),
    (lambda m: m['lags'].update({'new': {'type': 'static', 'id': 9, 'ports': ['1/1/9']}}), [('lag_missing', True)]),
    (lambda m: m['lags']['MCT_lag1'].update(id=3), [('lag_differs', False)]),
    (lambda m: m['lags']['MCT_lag1']['ports'].append('1/1/9'), [('lag_differs', False)]),
    (lambda m: m['lags']['MCT_lag1'].update(deployed=False), [('lag_deployed', False)]),
    (lambda m: m['lags'].pop('lag_agg_a_2'), [('lag_unmanaged', False)]),
    (lambda m: m['clusters']['ALB-CORE'].update(rbridge_id=5), [('cluster_differs', False)]),
    (lambda m: m['clusters']['ALB-CORE']['peers'][0].update(address='10.0.1.9'), [('cluster_differs', False)]),
    (lambda m: m['clusters']['ALB-CORE']['icl'].update(ports=['1/1/8']), [('cluster_differs', False)]),
    (lambda m: m['clusters']['ALB-CORE']['clients'].update(
        {'c2960x': {'rbridge_id': 201, 'interfaces': ['1/1/3'], 'deployed': True}}), [('client_missing', True)]),
    (lambda m: m['clusters']['ALB-CORE']['clients']['c2960g'].update(rbridge_id=201), [('client_differs', False)]),
    (lambda m: m['clusters']['ALB-CORE']['clients'].pop('c2960g'), [('client_unmanaged', False)]),
    (lambda m: m['clusters'].update({'OTHER': {'id': 1, 'rbridge_id': 1, 'session_vlan': 3000,
                                               'icl': {'name': 'X', 'ports': ['1/1/7']}}}),
     [('cluster_missing', True)]),
])
def test_drift(cs1a, model, edit, expected):
    edit(model)
    assert rules(config_drift(cs1a, model)) == expected


def test_deploying_what_is_not_deployed(cs1a, model):
    running = (cs1a.replace(' primary-port 1/1/7\n deploy\n', ' primary-port 1/1/7\n')
               .replace('  client-interface ethernet 1/1/2\n  deploy\n', '  client-interface ethernet 1/1/2\n')
               .replace(' peer 10.0.1.3 rbridge-id 3 icl ALB-CORE-MCT\n deploy\n',
                        ' peer 10.0.1.3 rbridge-id 3 icl ALB-CORE-MCT\n'))
    assert rules(config_drift(running, model)) == [
        ('client_deployed', True), ('cluster_deployed', True), ('lag_deployed', True)]
    assert config_commands(running, model) == [
        'lag "MCT_lag1" static id 2', ' deploy',
        'cluster ALB-CORE 4000', ' deploy', ' client c2960g', '  deploy']


def test_commands_only_add(cs1a, model):
    model['vlans'][1000]['tagged'] = ['1/1/2', '1/1/3', '1/1/4']   # 1/1/7-8 are extra, 1/1/3-4 missing
    model['vlans'][3000]['name'] = 'Session'
    model['ve_addresses'][3000] = ['10.0.1.9/24']
    model['clusters']['ALB-CORE']['rbridge_id'] = 5
    model['clusters']['ALB-CORE']['clients']['c2960x'] = {'rbridge_id': 201, 'interfaces': ['1/1/3'],
                                                          'deployed': True}
    assert config_commands(cs1a, model) == [
        'vlan 1000', ' tagged ethe 1/1/3 to 1/1/4',
        'vlan 3000 name Session',
        'interface ve 3000', ' ip address 10.0.1.9 255.255.255.0',
        'cluster ALB-CORE 4000', ' client c2960x', '  rbridge-id 201', '  client-interface ethernet 1/1/3',
        '  deploy',
    ]


def test_no_commands_for_a_cluster_with_another_id(cs1a, model):
    model['clusters']['ALB-CORE']['id'] = 4001
    model['clusters']['ALB-CORE']['clients']['c2960x'] = {'rbridge_id': 201, 'interfaces': ['1/1/3'],
                                                          'deployed': True}
    assert config_commands(cs1a, model) == []


def test_models_can_be_checked_like_running_configurations():
    # The models of an MCT pair, checked before anything is pushed.
    a = normalize_model(model_of(fixture('cs1a_running_config.txt')))[0]
    b = normalize_model(model_of(fixture('cs1b_running_config.txt')))[0]
    assert sorted(f['rule'] for f in mct_audit(a)) == ['keep_alive_no_separate_link', 'keep_alive_on_icl']
    assert mct_compare(a, b) == []
    b['clusters']['ALB-CORE']['rbridge_id'] = 2
    assert 'rbridge_id_duplicate' in [f['rule'] for f in mct_compare(a, b)]


def test_moving_keep_alive_off_the_icl(cs1a, model):
    # Keep-alive VLAN 3001 moves from the ICL (1/1/7-8) to its own link.
    model['vlans'][3001]['tagged'] = ['1/1/40']
    assert config_commands(cs1a, model) == ['vlan 3001', ' tagged ethe 1/1/40']
    # 1/1/7-8 are the ICL's LAG, changed through its primary port.
    assert config_removals(cs1a, model) == ['vlan 3001', ' no tagged ethe 1/1/7']


def test_removals_cover_tagged_and_untagged_ports(cs1a, model):
    running = cs1a.replace('vlan 1000 by port\n', 'vlan 1000 by port\n untagged ethe 1/1/20\n')
    model['vlans'][1000]['tagged'] = ['1/1/2']
    assert config_removals(running, model) == [
        'vlan 1000', ' no tagged ethe 1/1/7', ' no untagged ethe 1/1/20']


def test_nothing_to_remove(cs1a, model):
    assert config_removals(cs1a, model) == []


def test_vlans_not_in_the_model_are_left_alone(cs1a, model):
    del model['vlans'][1000]
    assert config_removals(cs1a, model) == []


def test_session_vlan_is_protected(cs1a, model):
    model['vlans'][3000]['tagged'] = ['1/1/7']
    assert config_removals(cs1a, model) == []
    [finding] = config_drift(cs1a, model, remove=['vlan_ports'])
    assert (finding['rule'], finding['fixable']) == ('vlan_ports_extra', False)
    assert finding['message'] == 'VLAN 3000 has extra tagged port(s) 1/1/8 (protected VLAN)'


def test_protected_vlans_are_left_alone(cs1a, model):
    model['vlans'][1000]['tagged'] = ['1/1/2']
    assert config_removals(cs1a, model, protected_vlans=[1000]) == []
    findings = config_drift(cs1a, model, remove=['vlan_ports'], protected_vlans=[1000])
    assert rules(findings) == [('vlan_ports_extra', False)]


def test_extra_ports_are_fixable_only_when_removal_is_asked_for(cs1a, model):
    model['vlans'][1000]['tagged'] = ['1/1/2']
    assert rules(config_drift(cs1a, model)) == [('vlan_ports_extra', False)]
    assert rules(config_drift(cs1a, model, remove=['vlan_ports'])) == [('vlan_ports_extra', True)]


@pytest.mark.parametrize('kwargs, message', [
    ({'remove': ['vlans']}, 'remove must be a list of: vlan_ports'),
    ({'remove': 'vlan_ports'}, 'remove must be a list of: vlan_ports'),
    ({'protected_vlans': 10}, 'protected_vlans must be a list'),
    ({'protected_vlans': ['ten']}, 'protected VLAN'),
])
def test_invalid_removal_settings(cs1a, model, kwargs, message):
    with pytest.raises(FastIronConfigError, match=message):
        config_drift(cs1a, model, **kwargs)


def test_part_of_a_deployed_lag_cannot_be_removed(cs1a, model):
    model['vlans'][1000]['tagged'] = ['1/1/2', '1/1/7']
    assert config_removals(cs1a, model) == []
    [finding] = config_drift(cs1a, model, remove=['vlan_ports'])
    assert (finding['rule'], finding['fixable']) == ('vlan_ports_extra', False)
    assert finding['message'] == ('VLAN 1000 has extra tagged port(s) 1/1/8 (part of deployed LAG MCT_lag1; '
                                  'list all of its ports or none)')


def test_ports_of_an_undeployed_lag_are_removed_one_by_one(cs1a, model):
    running = cs1a.replace(' primary-port 1/1/7\n deploy\n', ' primary-port 1/1/7\n')
    model['vlans'][1000]['tagged'] = ['1/1/2']
    assert config_removals(running, model) == ['vlan 1000', ' no tagged ethe 1/1/7 to 1/1/8']


def test_keep_alive_changes_need_the_cluster_undeployed(cs1a, model):
    model['vlans'][3001]['tagged'] = ['1/1/40']
    assert clusters_to_undeploy(cs1a, model, remove=['vlan_ports']) == [{'name': 'ALB-CORE', 'id': 4000}]
    # Adding a port alone needs it too.
    model['vlans'][3001]['tagged'] = ['1/1/7', '1/1/8', '1/1/40']
    assert clusters_to_undeploy(cs1a, model) == [{'name': 'ALB-CORE', 'id': 4000}]


@pytest.mark.parametrize('edit, kwargs', [
    (lambda m: None, {'remove': ['vlan_ports']}),
    (lambda m: m['vlans'][1000].update(tagged=['1/1/2', '1/1/7', '1/1/8', '1/1/9']), {'remove': ['vlan_ports']}),
    (lambda m: m['vlans'][3001].update(tagged=[]), {}),
    (lambda m: m['vlans'][3001].update(tagged=[]), {'remove': ['vlan_ports'], 'protected_vlans': [3001]}),
])
def test_no_undeploy_without_keep_alive_changes(cs1a, model, edit, kwargs):
    edit(model)
    assert clusters_to_undeploy(cs1a, model, **kwargs) == []


def test_no_undeploy_for_an_undeployed_cluster(cs1a, model):
    running = cs1a.replace(' peer 10.0.1.3 rbridge-id 3 icl ALB-CORE-MCT\n deploy\n',
                           ' peer 10.0.1.3 rbridge-id 3 icl ALB-CORE-MCT\n')
    assert running != cs1a
    model['vlans'][3001]['tagged'] = ['1/1/40']
    assert clusters_to_undeploy(running, model, remove=['vlan_ports']) == []


# system-max settings


def with_system_max(text, line):
    return text.replace('ver 08.0.30uT323\n', 'ver 08.0.30uT323\n!\n%s\n' % line, 1)


def test_parse_system_max(cs1a):
    assert parse_config(cs1a)['system_max'] == {}
    assert parse_config(with_system_max(cs1a, 'system-max vlan 1024'))['system_max'] == {'vlan': 1024}


def test_system_max_unset_is_drift_with_a_command(cs1a, model):
    model['system_max'] = {'vlan': 1024}
    findings = config_drift(cs1a, model)
    assert rules(findings) == [('system_max', True)]
    assert 'is the default, should be 1024' in findings[0]['message']
    assert config_commands(cs1a, model) == ['system-max vlan 1024']


def test_system_max_changed_value(cs1a, model):
    model['system_max'] = {'vlan': 1024}
    running = with_system_max(cs1a, 'system-max vlan 256')
    assert 'is 256, should be 1024' in config_drift(running, model)[0]['message']
    assert config_commands(running, model) == ['system-max vlan 1024']


def test_system_max_matching_is_clean(cs1a, model):
    model['system_max'] = {'vlan': 1024}
    running = with_system_max(cs1a, 'system-max vlan 1024')
    assert config_drift(running, model) == []
    assert config_commands(running, model) == []


def test_system_max_comes_before_vlans(model):
    model['system_max'] = {'vlan': 1024}
    commands = config_commands(EMPTY, model)
    assert commands[0] == 'system-max vlan 1024'
    rebuilt = 'Current configuration:\n!\n%s\nend\n' % '\n'.join(commands)
    assert config_drift(rebuilt, model) == []


def test_system_max_not_in_model_is_left_alone(cs1a, model):
    running = with_system_max(cs1a, 'system-max vlan 1024')
    assert config_drift(running, model) == []
    model['system_max'] = {}
    assert rules(config_drift(running, model)) == [('system_max_unmanaged', False)]
    assert config_commands(running, model) == []


@pytest.mark.parametrize('system_max', [
    {'vlan': 0},
    {'vlan': 'many'},
    {'vlan': True},
    {'ip route': 1024},
    {'VLAN': 1024},
    [1024],
])
def test_system_max_validation(model, system_max):
    model['system_max'] = system_max
    with pytest.raises(FastIronConfigError):
        normalize_model(model)


# Ethernet interface settings

INTERFACES = '''Current configuration:
!
ver 08.0.30uT7f3
!
vlan 1 name DEFAULT-VLAN by port
!
vlan 11 name Wireless by port
 tagged ethe 1/3/3
!
interface ethernet 1/1/1
 port-name "Shentel handoff"
!
interface ethernet 1/1/2
 port-name em0.rtr1.bee
!
interface ethernet 1/1/45
 port-name ap1.bee
 inline power priority 1
!
interface ethernet 1/3/2
 speed-duplex 10G-full
!
interface ethernet 1/3/3
 port-name ix0.gw2.bee
 dual-mode
 speed-duplex 10G-full
!
interface ethernet 1/3/4
 dual-mode 100
!
end
'''


def test_parse_interfaces():
    interfaces = parse_config(INTERFACES)['interfaces']
    assert interfaces['1/1/1'] == {'name': 'Shentel handoff', 'speed_duplex': None, 'dual_mode': False}
    assert interfaces['1/1/45'] == {'name': 'ap1.bee', 'speed_duplex': None, 'dual_mode': False}
    assert interfaces['1/3/2'] == {'name': None, 'speed_duplex': '10G-full', 'dual_mode': False}
    assert interfaces['1/3/3'] == {'name': 'ix0.gw2.bee', 'speed_duplex': '10G-full', 'dual_mode': True}
    assert interfaces['1/3/4']['dual_mode'] == 100
    assert '1/1/3' not in interfaces


def test_interfaces_match_themselves():
    model = {'interfaces': parse_config(INTERFACES)['interfaces']}
    assert config_drift(INTERFACES, model) == []
    assert config_commands(INTERFACES, model) == []


def test_interface_settings_left_out_are_not_compared():
    model = {'interfaces': {'1/3/3': {'name': 'ix0.gw2.bee'}, '1/1/9': {}}}
    assert config_drift(INTERFACES, model) == []
    assert config_commands(INTERFACES, model) == []


@pytest.mark.parametrize('interface, expected, commands', [
    ({'1/1/2': {'name': 'aggr0 rtr1.bee'}}, [('interface_name', True)],
     ['interface ethernet 1/1/2', ' port-name "aggr0 rtr1.bee"']),
    ({'1/1/3': {'name': 'em0.rtr1.bee'}}, [('interface_name', True)],
     ['interface ethernet 1/1/3', ' port-name em0.rtr1.bee']),
    ({'1/3/5': {'speed_duplex': '10G-full'}}, [('interface_speed_duplex', True)],
     ['interface ethernet 1/3/5', ' speed-duplex 10G-full']),
    ({'1/3/2': {'speed_duplex': '1000-full'}}, [('interface_speed_duplex', True)],
     ['interface ethernet 1/3/2', ' speed-duplex 1000-full']),
    ({'1/3/2': {'dual_mode': True}}, [('interface_dual_mode', True)], ['interface ethernet 1/3/2', ' dual-mode']),
    ({'1/3/2': {'dual_mode': 100}}, [('interface_dual_mode', True)], ['interface ethernet 1/3/2', ' dual-mode 100']),
    # Turning dual-mode off or moving it to another VLAN is done by hand.
    ({'1/3/3': {'dual_mode': False}}, [('interface_dual_mode', False)], []),
    ({'1/3/4': {'dual_mode': True}}, [('interface_dual_mode', False)], []),
])
def test_interface_drift_and_commands(interface, expected, commands):
    model = {'interfaces': interface}
    findings = config_drift(INTERFACES, model)
    assert rules(findings) == expected
    assert config_commands(INTERFACES, model) == commands


def test_interface_commands_are_ordered_around_lags_and_vlans():
    # A LAG's ports must match before it is deployed, and dual-mode needs the
    # port tagged in a VLAN first.
    model = {
        'interfaces': {'1/3/5': {'name': 'ix0.gw2.bee', 'speed_duplex': '10G-full', 'dual_mode': True},
                       '1/3/6': {'name': 'ix1.gw2.bee', 'speed_duplex': '10G-full'}},
        'lags': {'gw2': {'type': 'dynamic', 'id': 12, 'ports': ['1/3/5', '1/3/6']}},
        'vlans': {11: {'name': 'Wireless', 'tagged': ['1/3/3', '1/3/5', '1/3/6']}},
    }
    commands = config_commands(INTERFACES, model)
    assert commands == [
        'interface ethernet 1/3/5', ' port-name ix0.gw2.bee', ' speed-duplex 10G-full',
        'interface ethernet 1/3/6', ' port-name ix1.gw2.bee', ' speed-duplex 10G-full',
        'lag "gw2" dynamic id 12', ' ports ethernet 1/3/5 to 1/3/6', ' primary-port 1/3/5', ' deploy',
        'vlan 11', ' tagged ethe 1/3/5 to 1/3/6',
        'interface ethernet 1/3/5', ' dual-mode',
    ]


def test_interface_commands_read_back_as_the_model():
    model = {'interfaces': {'1/1/2': {'name': 'two words', 'speed_duplex': '1000-full', 'dual_mode': 7}}}
    commands = config_commands(EMPTY, model)
    rebuilt = 'Current configuration:\n!\n%s\nend\n' % '\n'.join(commands)
    assert config_drift(rebuilt, model) == []


@pytest.mark.parametrize('interfaces, message', [
    ({'1/1/1 to 1/1/2': {}}, 'single ports'),
    ({'ve 1': {}}, 'single ports'),
    ({'1/1/1': {'mtu': 9000}}, 'unknown keys: mtu'),
    ({'1/1/1': {'name': ''}}, 'name must be text'),
    ({'1/1/1': {'name': 'say "hi"'}}, 'name must be text'),
    ({'1/1/1': {'speed_duplex': '10G full'}}, 'speed_duplex must be a word'),
    ({'1/1/1': {'dual_mode': 5000}}, 'dual_mode must be true, false or a VLAN id'),
    ({'1/1/1': {'dual_mode': 'yes'}}, 'dual_mode must be a number'),
])
def test_invalid_interfaces(interfaces, message):
    with pytest.raises(FastIronConfigError, match=message):
        normalize_model({'interfaces': interfaces})


def test_dual_mode_belongs_on_a_lags_primary_port():
    model = {'interfaces': {'1/3/6': {'dual_mode': True}},
             'lags': {'gw2': {'type': 'dynamic', 'id': 12, 'ports': ['1/3/5', '1/3/6']}}}
    with pytest.raises(FastIronConfigError, match='set dual_mode on its primary port 1/3/5'):
        normalize_model(model)
    model['interfaces'] = {'1/3/5': {'dual_mode': True}, '1/3/6': {'name': 'ix1.gw2.bee'}}
    normalize_model(model)
