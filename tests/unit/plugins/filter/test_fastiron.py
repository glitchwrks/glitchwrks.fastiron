# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os

import pytest

from ansible.errors import AnsibleFilterError

from ansible_collections.glitchwrks.fastiron.plugins.filter.fastiron import FilterModule

FIXTURES = os.path.join(os.path.dirname(__file__), '..', '..', 'fixtures')
FILTERS = FilterModule().filters()


@pytest.fixture
def cs1a():
    with open(os.path.join(FIXTURES, 'cs1a_running_config.txt')) as f:
        return f.read()


def test_model_filters_take_text_or_parsed_configurations(cs1a):
    model = {'vlans': {3000: {'name': 'Session-VLAN', 'tagged': ['1/1/7', '1/1/8'], 'router_interface': 3000}}}
    parsed = FILTERS['fastiron_parse_config'](cs1a)
    for config in (cs1a, parsed):
        drift = FILTERS['fastiron_config_drift'](config, model)
        assert sorted(f['rule'] for f in drift) == ['vlan_unmanaged', 'vlan_unmanaged', 'vlan_unmanaged']
        assert FILTERS['fastiron_config_commands'](config, model) == []


def test_model_config_returns_the_parsed_shape():
    config = FILTERS['fastiron_model_config']({'vlans': {10: {}}})
    assert config['vlans'] == {10: {'name': None, 'tagged': [], 'untagged': [], 'router_interface': None}}
    assert set(config) == {'hostname', 'version', 'vlans', 'lags', 've_addresses', 'clusters'}


@pytest.mark.parametrize('name, args', [
    ('fastiron_model_config', ({'vlans': {'x': {}}},)),
    ('fastiron_config_drift', ('end\n', {'lags': {'l': {'type': 'lacp'}}})),
    ('fastiron_config_commands', ('end\n', {'clusters': {'C': {}}})),
])
def test_invalid_models_are_filter_errors(name, args):
    with pytest.raises(AnsibleFilterError, match='^%s: ' % name):
        FILTERS[name](*args)


def test_mct_health_takes_text_or_parsed_status():
    filters = FilterModule().filters()
    with open(os.path.join(FIXTURES, 'cs1b_show_cluster.txt')) as f:
        status = f.read()
    with open(os.path.join(FIXTURES, 'cs1b_running_config.txt')) as f:
        config = f.read()
    parsed = filters['fastiron_parse_cluster_status'](status)
    assert filters['fastiron_mct_health'](status) == []
    assert filters['fastiron_mct_health'](parsed, config) == []
    assert filters['fastiron_mct_health'](status, filters['fastiron_parse_config'](config)) == []


def test_mct_health_rejects_other_input():
    with pytest.raises(AnsibleFilterError, match='fastiron_mct_health'):
        FilterModule().filters()['fastiron_mct_health'](42)


def test_removals_filter(cs1a):
    filters = FilterModule().filters()
    model = filters['fastiron_parse_config'](cs1a)
    model = {key: model[key] for key in ('vlans', 'lags', 've_addresses', 'clusters')}
    model['vlans'][3001]['tagged'] = ['1/1/40']
    assert filters['fastiron_config_removals'](cs1a, model) == ['vlan 3001', ' no tagged ethe 1/1/7']
    assert filters['fastiron_config_undeploy'](cs1a, model, ['vlan_ports']) == [{'name': 'ALB-CORE', 'id': 4000}]
    assert filters['fastiron_config_removals'](cs1a, model, protected_vlans=[3001]) == []
    with pytest.raises(AnsibleFilterError, match='fastiron_config_drift: remove must be'):
        filters['fastiron_config_drift'](cs1a, model, remove=['everything'])


def test_mct_health_takes_interfaces_text(cs1a):
    with open(os.path.join(FIXTURES, 'cs1a_show_interface_brief_icl_link_down.txt')) as f:
        interfaces = f.read()
    findings = FilterModule().filters()['fastiron_mct_health']('', cs1a, interfaces)
    assert 'icl_port_down' in [f['rule'] for f in findings]
