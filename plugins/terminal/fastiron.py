# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

DOCUMENTATION = r'''
author: Jonathan Chapman (@glitchwrks)
name: fastiron
short_description: Terminal plugin for Brocade / Ruckus FastIron switches
version_added: 0.1.0
description:
  - Recognizes FastIron prompts and error messages, turns off paging, and enters
    privileged EXEC mode with the enable password.
  - Use with P(ansible.netcommon.network_cli#connection) by setting C(ansible_network_os) to
    C(glitchwrks.fastiron.fastiron).
'''

import json
import re

from ansible.errors import AnsibleConnectionFailure
from ansible.module_utils.common.text.converters import to_bytes, to_text

from ansible_collections.ansible.netcommon.plugins.plugin_utils.terminal_base import TerminalBase


class TerminalModule(TerminalBase):

    # SSH@cs1a.example.net>   SSH@cs1a.example.net#   SSH@cs1a.example.net(config-cluster-CORE)#
    # Console sessions omit the "SSH@" / "telnet@" prefix. Before a hostname is set,
    # the prompt is the model plus " Router" or " Switch", e.g. "ICX6650-64 Router#".
    terminal_stdout_re = [
        re.compile(br"[\r\n]?(?:[\w-]+@)?[\w.\-:/\[\]]+(?: Router| Switch)?(?:\([^)\r\n]*\))?[>#] ?$"),
    ]

    terminal_stderr_re = [
        re.compile(br"Invalid input ->"),
        re.compile(br"Incomplete command"),
        re.compile(br"Ambiguous input"),
        re.compile(br"Unrecognized command"),
        re.compile(br"[\r\n]Error[ :-]"),
        re.compile(br"% ?Error"),
        re.compile(br"Not authorized to execute this command"),
        re.compile(br"Permission denied", re.I),
    ]

    def on_open_shell(self):
        if self._get_prompt().endswith(b'#'):
            self._disable_paging()
        else:
            # User EXEC may not allow this; on_become disables paging again after
            # entering privileged EXEC mode, where it is always available.
            try:
                self._exec_cli_command(b'skip-page-display')
            except AnsibleConnectionFailure:
                pass

    def _disable_paging(self):
        try:
            self._exec_cli_command(b'skip-page-display')
        except AnsibleConnectionFailure as e:
            raise AnsibleConnectionFailure('unable to disable paging: %s' % to_text(e))

    def on_become(self, passwd=None):
        if self._get_prompt().endswith(b'#'):
            return

        cmd = {
            u'command': u'enable',
            u'prompt': u'[\r\n]?[Pp]assword: ?$',
            u'answer': passwd,
            u'prompt_retry_check': True,
        }
        try:
            self._exec_cli_command(to_bytes(json.dumps(cmd), errors='surrogate_or_strict'))
        except AnsibleConnectionFailure as e:
            raise AnsibleConnectionFailure(
                'unable to enter privileged EXEC mode, at prompt [%s]: %s' % (to_text(self._get_prompt()), to_text(e)))

        prompt = self._get_prompt()
        if prompt is None or not prompt.endswith(b'#'):
            raise AnsibleConnectionFailure(
                'unable to enter privileged EXEC mode, still at prompt [%s]; check ansible_become_password'
                % to_text(prompt))
        self._disable_paging()

    def on_unbecome(self):
        prompt = self._get_prompt()
        if prompt is None:
            return
        if b'(config' in prompt:
            self._exec_cli_command(b'end')
