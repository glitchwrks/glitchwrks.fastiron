# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

DOCUMENTATION = r'''
author: Jonathan Chapman (@glitchwrks)
name: fastiron
short_description: Cliconf plugin for Brocade / Ruckus FastIron switches
version_added: 0.1.0
description:
  - Reads and changes the configuration of FastIron switches over the CLI, for use with
    M(ansible.netcommon.cli_command) and M(ansible.netcommon.cli_config).
  - Configuration diffs are generated on the controller by comparing the candidate with
    the running configuration, so M(ansible.netcommon.cli_config) in check mode reports
    drift without changing the switch.
  - Changes are applied line by line in configuration mode and are not saved to flash;
    run C(write memory) afterwards to keep them across a reload. The plugin waits for the
    copy to flash to finish before returning, so its output does not leak into the next
    command.
  - Use with P(ansible.netcommon.network_cli#connection) by setting C(ansible_network_os) to
    C(glitchwrks.fastiron.fastiron).
'''

import json
import re

from ansible.errors import AnsibleConnectionFailure
from ansible.module_utils.common.text.converters import to_bytes, to_text

from ansible_collections.ansible.netcommon.plugins.module_utils.network.common.config import NetworkConfig
from ansible_collections.ansible.netcommon.plugins.module_utils.network.common.utils import to_list
from ansible_collections.ansible.netcommon.plugins.plugin_utils.cliconf_base import (
    CliconfBase,
    enable_mode,
)

# FastIron configurations nest blocks with a single space.
CONFIG_INDENT = 1

# Lines in "show running-config" that are not configuration and must never be
# compared or pushed.
NON_CONFIG_LINES = [r'^Current configuration:', r'^ver ', r'^end$']

_SW_VERSION_RE = re.compile(r'SW: Version (\S+)')
_HW_RE = re.compile(r'HW: (?:Stackable )?(.+)')
_HOSTNAME_RE = re.compile(r'^hostname (\S+)', re.M)

# "write memory" prints "Write startup-config done." and the prompt, and only then
# copies to flash, printing the lines below with no prompt after them. Without
# waiting for them, they would show up in the output of the next command.
_WRITE_MEMORY_RE = re.compile(r'^\s*wr\w*\s+mem\w*\s*$', re.I)
_WRITE_DONE = 'Write startup-config done'
_FLASH_DONE = 'Flash to Flash Done'


def to_commands(items):
    """Turn configuration diff items into commands to type in configuration mode.

    FastIron stays in a block's sub-mode until told to leave, so after a nested
    block (a cluster's client definition, say) the next line for the enclosing
    block would be applied to the nested one. Emit "exit" to step back out to the
    right level before each line, and enter any parent blocks that are not
    already open.
    """
    commands = []
    current = []
    for item in items:
        target = [str(p) if not hasattr(p, 'text') else p.text for p in item.parents]
        while current and current != target[:len(current)]:
            commands.append('exit')
            current.pop()
        for parent in target[len(current):]:
            commands.append(parent)
            current.append(parent)
        commands.append(item.text)
        if item.has_children:
            current.append(item.text)
    return commands


class Cliconf(CliconfBase):

    def send_command(self, command=None, prompt=None, answer=None, sendonly=False, **kwargs):
        if prompt is not None or sendonly or not _WRITE_MEMORY_RE.match(to_text(command or '')):
            return super().send_command(command=command, prompt=prompt, answer=answer, sendonly=sendonly, **kwargs)

        # If the flash lines arrive together with the prompt, answering them with a
        # newline brings the prompt back; otherwise wait for them separately.
        response = super().send_command(command=command, prompt=_FLASH_DONE, answer='', **kwargs)
        if _WRITE_DONE in response and _FLASH_DONE not in response:
            flash = self._connection.receive(prompts=[to_bytes(_FLASH_DONE)], answer=[b''])
            response += '\n' + to_text(flash, errors='surrogate_then_replace')
        return response

    def get_device_info(self):
        device_info = {'network_os': 'fastiron'}
        version = to_text(self.get('show version'), errors='surrogate_or_strict')
        match = _SW_VERSION_RE.search(version)
        if match:
            device_info['network_os_version'] = match.group(1)
        match = _HW_RE.search(version)
        if match:
            device_info['network_os_model'] = match.group(1).strip()
        match = _HOSTNAME_RE.search(to_text(self.get_config(), errors='surrogate_or_strict'))
        if match:
            device_info['network_os_hostname'] = match.group(1)
        return device_info

    @enable_mode
    def get_config(self, source='running', flags=None, format=None):
        if source not in ('running', 'startup'):
            raise ValueError("fetching configuration from %s is not supported" % source)
        if format:
            raise ValueError("'format' value %s is not supported for get_config" % format)

        cmd = 'show running-config' if source == 'running' else 'show configuration'
        if flags:
            cmd += ' ' + ' '.join(to_list(flags))
        return self.send_command(cmd)

    @enable_mode
    def edit_config(self, candidate=None, commit=True, replace=None, diff=False, comment=None):
        if not candidate:
            raise ValueError("must provide a candidate config to load")
        if commit not in (True, False):
            raise ValueError("'commit' must be a bool, got %s" % commit)
        if replace not in (None, False):
            raise ValueError("configuration replace is not supported on FastIron")
        if comment:
            raise ValueError("commit comments are not supported on FastIron")

        requests = []
        responses = []
        if commit:
            self.send_command('configure terminal')
            try:
                for line in to_list(candidate):
                    if not isinstance(line, dict):
                        line = {'command': line}
                    if line['command'].strip() in ('', 'end'):
                        continue
                    responses.append(self.send_command(**line))
                    requests.append(line['command'])
            finally:
                self.send_command('end')
        return {'request': requests, 'response': responses}

    def get_diff(self, candidate=None, running=None, diff_match='line', diff_ignore_lines=None, path=None,
                 diff_replace='line'):
        option_values = self.get_option_values()
        if candidate is None:
            raise ValueError("candidate configuration is required to generate diff")
        if diff_match not in option_values['diff_match']:
            raise ValueError("'match' value %s is invalid, valid values are %s"
                             % (diff_match, ', '.join(option_values['diff_match'])))
        if diff_replace not in option_values['diff_replace']:
            raise ValueError("'replace' value %s is invalid, valid values are %s"
                             % (diff_replace, ', '.join(option_values['diff_replace'])))

        ignore_lines = NON_CONFIG_LINES + to_list(diff_ignore_lines)
        candidate_obj = NetworkConfig(indent=CONFIG_INDENT, ignore_lines=ignore_lines)
        candidate_obj.load(candidate)

        if running and diff_match != 'none':
            running_obj = NetworkConfig(indent=CONFIG_INDENT, contents=running, ignore_lines=ignore_lines)
            changes = candidate_obj.difference(running_obj, path=path, match=diff_match, replace=diff_replace)
        else:
            changes = candidate_obj.items

        return {'config_diff': '\n'.join(to_commands(changes)), 'banner_diff': {}}

    def get_device_operations(self):
        return {
            'supports_diff_replace': True,
            'supports_commit': False,
            'supports_rollback': False,
            'supports_defaults': False,
            'supports_onbox_diff': False,
            'supports_commit_comment': False,
            'supports_multiline_delimiter': False,
            'supports_diff_match': True,
            'supports_diff_ignore_lines': True,
            'supports_generate_diff': True,
            'supports_replace': False,
        }

    def get_option_values(self):
        return {
            'format': ['text'],
            'diff_match': ['line', 'strict', 'exact', 'none'],
            'diff_replace': ['line', 'block'],
            'output': [],
        }

    def get_capabilities(self):
        result = {
            'rpc': self.get_base_rpc() + ['get_diff', 'run_commands'],
            'network_api': 'cliconf',
            'device_info': self.get_device_info(),
            'device_operations': self.get_device_operations(),
        }
        result.update(self.get_option_values())
        return json.dumps(result)

    def set_cli_prompt_context(self):
        """Leave configuration mode if a previous task left the session there."""
        if self._connection.connected:
            out = self._connection.get_prompt()
            if out is None:
                raise AnsibleConnectionFailure(message=u'cli prompt is not identified from the last received'
                                                       u' response window: %s' % self._connection._last_recv_window)
            if b'(config' in out:
                self._connection.queue_message('vvvv', 'leaving configuration mode')
                self.send_command('end')
