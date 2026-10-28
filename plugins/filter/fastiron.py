# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from collections.abc import Mapping

from ansible.errors import AnsibleFilterError

from ansible_collections.glitchwrks.fastiron.plugins.plugin_utils.fastiron_config import (
    FastIronConfigError,
    mct_audit,
    mct_compare,
    parse_config,
)
from ansible_collections.glitchwrks.fastiron.plugins.plugin_utils.fastiron_model import (
    config_commands,
    config_drift,
    config_removals,
    normalize_model,
)
from ansible_collections.glitchwrks.fastiron.plugins.plugin_utils.fastiron_status import (
    mct_health,
    mct_health_compare,
    parse_cluster_status,
)


def _parsed(value, filter_name):
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            return parse_config(value)
        except FastIronConfigError as e:
            raise AnsibleFilterError('%s: %s' % (filter_name, e))
    raise AnsibleFilterError('%s: expected configuration text or a parsed configuration, got %s'
                             % (filter_name, type(value).__name__))


def fastiron_parse_config(text):
    return _parsed(text, 'fastiron_parse_config')


def fastiron_mct_audit(config):
    return mct_audit(_parsed(config, 'fastiron_mct_audit'))


def fastiron_mct_compare(config, peer_config, name='this switch', peer_name='its peer'):
    return mct_compare(_parsed(config, 'fastiron_mct_compare'), _parsed(peer_config, 'fastiron_mct_compare'),
                       name, peer_name)


def _model_call(filter_name, function, *args):
    try:
        return function(*args)
    except FastIronConfigError as e:
        raise AnsibleFilterError('%s: %s' % (filter_name, e))


def fastiron_model_config(model):
    return _model_call('fastiron_model_config', normalize_model, model)[0]


def fastiron_config_drift(config, model, remove=None, protected_vlans=None):
    return _model_call('fastiron_config_drift', config_drift, _parsed(config, 'fastiron_config_drift'), model,
                       remove, protected_vlans)


def fastiron_config_commands(config, model):
    return _model_call('fastiron_config_commands', config_commands, _parsed(config, 'fastiron_config_commands'),
                       model)


def _status(value, filter_name):
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        return parse_cluster_status(value)
    raise AnsibleFilterError('%s: expected "show cluster" output or parsed cluster status, got %s'
                             % (filter_name, type(value).__name__))


def fastiron_parse_cluster_status(text):
    return _status(text, 'fastiron_parse_cluster_status')


def fastiron_mct_health(status, config=None):
    return mct_health(_status(status, 'fastiron_mct_health'),
                      None if config is None else _parsed(config, 'fastiron_mct_health'))


def fastiron_mct_health_compare(status, peer_status, name='this switch', peer_name='its peer'):
    return mct_health_compare(_status(status, 'fastiron_mct_health_compare'),
                              _status(peer_status, 'fastiron_mct_health_compare'), name, peer_name)


def fastiron_config_removals(config, model, protected_vlans=None):
    return _model_call('fastiron_config_removals', config_removals, _parsed(config, 'fastiron_config_removals'),
                       model, protected_vlans)


class FilterModule(object):

    def filters(self):
        return {
            'fastiron_parse_config': fastiron_parse_config,
            'fastiron_mct_audit': fastiron_mct_audit,
            'fastiron_mct_compare': fastiron_mct_compare,
            'fastiron_model_config': fastiron_model_config,
            'fastiron_config_drift': fastiron_config_drift,
            'fastiron_config_commands': fastiron_config_commands,
            'fastiron_config_removals': fastiron_config_removals,
            'fastiron_parse_cluster_status': fastiron_parse_cluster_status,
            'fastiron_mct_health': fastiron_mct_health,
            'fastiron_mct_health_compare': fastiron_mct_health_compare,
        }
