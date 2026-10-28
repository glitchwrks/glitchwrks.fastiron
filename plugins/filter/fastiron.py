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
    normalize_model,
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


def fastiron_config_drift(config, model):
    return _model_call('fastiron_config_drift', config_drift, _parsed(config, 'fastiron_config_drift'), model)


def fastiron_config_commands(config, model):
    return _model_call('fastiron_config_commands', config_commands, _parsed(config, 'fastiron_config_commands'),
                       model)


class FilterModule(object):

    def filters(self):
        return {
            'fastiron_parse_config': fastiron_parse_config,
            'fastiron_mct_audit': fastiron_mct_audit,
            'fastiron_mct_compare': fastiron_mct_compare,
            'fastiron_model_config': fastiron_model_config,
            'fastiron_config_drift': fastiron_config_drift,
            'fastiron_config_commands': fastiron_config_commands,
        }
