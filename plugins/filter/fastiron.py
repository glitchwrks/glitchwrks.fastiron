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


class FilterModule(object):

    def filters(self):
        return {
            'fastiron_parse_config': fastiron_parse_config,
            'fastiron_mct_audit': fastiron_mct_audit,
            'fastiron_mct_compare': fastiron_mct_compare,
        }
