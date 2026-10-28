# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ansible.errors import AnsibleConnectionFailure

from ansible_collections.glitchwrks.fastiron.plugins.terminal.fastiron import TerminalModule


def matches(patterns, text):
    return any(p.search(text) for p in patterns)


# Prompts as captured from FastIron 08.0.30 on ICX 6650s, plus config-mode variants.
@pytest.mark.parametrize('prompt', [
    b'\r\nSSH@cs1a.example.net#',
    b'\r\nSSH@cs1b.example.net>',
    b'\r\nSSH@cs1b.example.net(config)#',
    b'\r\nSSH@cs1a.example.net(config-cluster-ALB-CORE)#',
    b'\r\nSSH@cs1a.example.net(config-cluster-ALB-CORE-client-c2960g)#',
    b'\r\nSSH@cs1a.example.net(config-if-e10000-1/1/7)#',
    b'\r\nSSH@cs1a.example.net(config-lag-MCT_lag1)#',
    b'\r\nSSH@sw-core-02.example.net>',
    # Factory prompts, before a hostname is set
    b'\r\nSSH@ICX6650-64 Router#',
    b'\r\nSSH@ICX6650-64 Router(config)#',
    b'\r\nICX6450-24 Switch>',
])
def test_prompts_match(prompt):
    assert matches(TerminalModule.terminal_stdout_re, prompt)


@pytest.mark.parametrize('line', [
    b'\r\nWrite startup-config done.',
    b'\r\nFlash to Flash Done.',
    b'\r\nSTACKID 1  system uptime is 6 days 5 hours 39 minutes 40 seconds',
    b'\r\n tagged ethe 1/1/2 ethe 1/1/7 to 1/1/8',
    b'\r\n!',
    b'\r\nNo password has been assigned yet...',
])
def test_output_is_not_mistaken_for_a_prompt(line):
    assert not matches(TerminalModule.terminal_stdout_re, line)


@pytest.mark.parametrize('output', [
    b'Invalid input -> shw version\r\nType ? for a list',
    b'Incomplete command.',
    b'Ambiguous input -> sh',
    b'\r\nError - VLAN 4095 is reserved',
])
def test_errors_are_detected(output):
    assert matches(TerminalModule.terminal_stderr_re, output)


def test_normal_output_is_not_an_error():
    assert not matches(TerminalModule.terminal_stderr_re, b'Write startup-config done.\r\nFlash to Flash Done.')


def strip(terminal, data):
    """network_cli's cleanup of each piece of a response."""
    for regex in terminal.ansi_re:
        data = regex.sub(b'', data)
    return data


ERASED = b'--More--, next page: Space, next line: Return key, quit: Control-c' + b'\x08 \x08' * 66 + b' router-interface ve 1'


def test_user_exec_keeps_the_pager_erase_sequence():
    connection = MagicMock()
    connection.get_prompt.return_value = b'SSH@cs1a.example.net>'
    connection.exec_command.side_effect = AnsibleConnectionFailure('Invalid input -> skip-page-display')
    terminal = TerminalModule(connection)
    terminal.on_open_shell()
    assert strip(terminal, ERASED) == ERASED


def test_privileged_exec_keeps_the_default_cleanup():
    connection = MagicMock()
    connection.get_prompt.return_value = b'SSH@cs1a.example.net>'
    connection.exec_command.side_effect = AnsibleConnectionFailure('Invalid input -> skip-page-display')
    terminal = TerminalModule(connection)
    terminal.on_open_shell()
    connection.exec_command.side_effect = None
    connection.get_prompt.side_effect = [b'SSH@cs1a.example.net>', b'SSH@cs1a.example.net#']
    terminal.on_become(passwd='secret')
    assert b'\x08' not in strip(terminal, ERASED)
    assert len(terminal.ansi_re) == len(TerminalModule._ANSI_RE)
