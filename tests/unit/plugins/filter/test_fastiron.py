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
