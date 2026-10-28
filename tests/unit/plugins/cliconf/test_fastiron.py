# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from ansible_collections.glitchwrks.fastiron.plugins.cliconf.fastiron import Cliconf

FIXTURES = os.path.join(os.path.dirname(__file__), '..', '..', 'fixtures')


def fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


@pytest.fixture
def cliconf():
    return Cliconf(MagicMock())


@pytest.fixture
def running():
    return fixture('cs1a_running_config.txt')


def diff(cliconf, candidate, running, **kwargs):
    return cliconf.get_diff(candidate=candidate, running=running, **kwargs)['config_diff']


def test_config_already_present_has_no_diff(cliconf, running):
    candidate = '\n'.join([
        'vlan 1000 by port',
        ' tagged ethe 1/1/2 ethe 1/1/7 to 1/1/8',
        'cluster ALB-CORE 4000',
        ' rbridge-id 2',
        ' client c2960g',
        '  rbridge-id 200',
        '  deploy',
        'hostname cs1a.example.net',
    ])
    assert diff(cliconf, candidate, running) == ''


def test_running_config_against_itself_has_no_diff(cliconf, running):
    assert diff(cliconf, running, running) == ''


def test_new_top_level_block(cliconf, running):
    candidate = 'vlan 2000 name Lab by port\n tagged ethe 1/1/7 to 1/1/8\n'
    assert diff(cliconf, candidate, running).splitlines() == [
        'vlan 2000 name Lab by port', 'tagged ethe 1/1/7 to 1/1/8']


def test_change_inside_nested_cluster_client_block(cliconf, running):
    candidate = 'cluster ALB-CORE 4000\n client c2960g\n  rbridge-id 201\n'
    assert diff(cliconf, candidate, running).splitlines() == [
        'cluster ALB-CORE 4000', 'client c2960g', 'rbridge-id 201']


def test_steps_out_of_nested_blocks_before_shallower_lines(cliconf, running):
    # Without the exits, the cluster's rbridge-id would be applied to the client,
    # since the switch is still in the client sub-mode.
    candidate = '\n'.join([
        'cluster ALB-CORE 4000',
        ' client c2960g',
        '  rbridge-id 201',
        ' rbridge-id 5',
        'vlan 2000 by port',
        ' tagged ethe 1/1/7',
        'hostname cs1z.example.net',
    ])
    assert diff(cliconf, candidate, running).splitlines() == [
        'cluster ALB-CORE 4000',
        'client c2960g',
        'rbridge-id 201',
        'exit',
        'rbridge-id 5',
        'exit',
        'vlan 2000 by port',
        'tagged ethe 1/1/7',
        'exit',
        'hostname cs1z.example.net',
    ]


def test_lines_the_switch_pads_with_extra_spaces_match(cliconf, running):
    running += 'ip ssh  password-authentication no\nip ssh  interactive-authentication no\n'
    candidate = 'ip ssh password-authentication no\nip ssh interactive-authentication no\n'
    assert diff(cliconf, candidate, running) == ''


def test_extra_spaces_in_the_candidate_are_collapsed_and_nesting_kept(cliconf, running):
    candidate = 'cluster ALB-CORE 4000\n client  c2960h\n  rbridge-id\t201\n'
    assert diff(cliconf, candidate, running).splitlines() == [
        'cluster ALB-CORE 4000', 'client c2960h', 'rbridge-id 201']


def test_diff_ignore_lines(cliconf, running):
    candidate = 'hostname cs1a.example.net\nlogging buffered 500\n'
    assert diff(cliconf, candidate, running, diff_ignore_lines=[r'^logging ']) == ''


def test_diff_match_none_pushes_everything(cliconf, running):
    candidate = 'hostname cs1a.example.net\n'
    assert diff(cliconf, candidate, running, diff_match='none') == 'hostname cs1a.example.net'


def test_invalid_diff_match(cliconf, running):
    with pytest.raises(ValueError):
        diff(cliconf, 'hostname x', running, diff_match='bogus')


def test_capabilities(cliconf):
    cliconf.get = MagicMock(return_value=fixture('show_version.txt'))
    cliconf._connection.get_prompt.return_value = b'SSH@cs1a.example.net#'
    cliconf.send_command = MagicMock(return_value=fixture('cs1a_running_config.txt'))
    caps = json.loads(cliconf.get_capabilities())
    assert caps['network_api'] == 'cliconf'
    assert caps['device_info'] == {'network_os': 'fastiron', 'network_os_version': '08.0.30uT323',
                                   'network_os_model': 'ICX6650-64', 'network_os_hostname': 'cs1a.example.net'}
    assert caps['device_operations']['supports_generate_diff'] is True
    assert caps['device_operations']['supports_replace'] is False
    assert 'get_diff' in caps['rpc']


def test_edit_config_wraps_lines_in_config_mode(cliconf):
    cliconf._connection.get_prompt.return_value = b'SSH@cs1a.example.net#'
    cliconf.send_command = MagicMock(return_value='')
    result = cliconf.edit_config(candidate=['vlan 2000 by port', ' tagged ethe 1/1/7 to 1/1/8', 'end'])
    sent = [c.kwargs.get('command', c.args[0] if c.args else None) for c in cliconf.send_command.call_args_list]
    assert sent == ['configure terminal', 'vlan 2000 by port', ' tagged ethe 1/1/7 to 1/1/8', 'end']
    assert result['request'] == ['vlan 2000 by port', ' tagged ethe 1/1/7 to 1/1/8']


def test_edit_config_without_commit_sends_nothing(cliconf):
    cliconf._connection.get_prompt.return_value = b'SSH@cs1a.example.net#'
    cliconf.send_command = MagicMock()
    cliconf.edit_config(candidate=['hostname x'], commit=False)
    cliconf.send_command.assert_not_called()


def test_edit_config_refuses_replace(cliconf):
    cliconf._connection.get_prompt.return_value = b'SSH@cs1a.example.net#'
    with pytest.raises(ValueError):
        cliconf.edit_config(candidate=['hostname x'], replace=True)


def test_config_requires_privileged_mode(cliconf):
    cliconf._connection.get_prompt.return_value = b'SSH@cs1a.example.net>'
    with pytest.raises(Exception, match='privilege escalation'):
        cliconf.get_config()


@pytest.mark.parametrize('command', ['write memory', 'wri mem', 'wr mem'])
def test_write_memory_waits_for_flash_lines(cliconf, command):
    cliconf._connection.send.return_value = 'Write startup-config done.'
    cliconf._connection.receive.return_value = b'Flash Memory Write (8192 bytes per dot) .\nFlash to Flash Done.'
    out = cliconf.get(command)
    assert out.splitlines() == [
        'Write startup-config done.', 'Flash Memory Write (8192 bytes per dot) .', 'Flash to Flash Done.']
    assert cliconf._connection.send.call_args.kwargs['prompt'] == b'Flash to Flash Done'
    assert cliconf._connection.receive.call_args.kwargs['prompts'] == [b'Flash to Flash Done']


def test_write_memory_with_flash_lines_already_received(cliconf):
    cliconf._connection.send.return_value = 'Write startup-config done.\nFlash to Flash Done.'
    assert cliconf.get('write memory') == 'Write startup-config done.\nFlash to Flash Done.'
    cliconf._connection.receive.assert_not_called()


def test_failed_write_memory_does_not_wait(cliconf):
    cliconf._connection.send.return_value = 'Error: flash is busy'
    assert cliconf.get('write memory') == 'Error: flash is busy'
    cliconf._connection.receive.assert_not_called()


@pytest.mark.parametrize('kwargs', [{'command': 'show running-config'},
                                    {'command': 'write memory', 'prompt': 'something', 'answer': 'y'}])
def test_other_commands_are_sent_unchanged(cliconf, kwargs):
    cliconf._connection.send.return_value = 'output'
    assert cliconf.get(**kwargs) == 'output'
    assert 'prompt' not in kwargs or cliconf._connection.send.call_args.kwargs['prompt'] == b'something'
    cliconf._connection.receive.assert_not_called()
