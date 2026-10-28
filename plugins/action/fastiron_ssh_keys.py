# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import tempfile

from ansible.module_utils.common.text.converters import to_text
from ansible.module_utils.connection import Connection, ConnectionError
from ansible.plugins.action import ActionBase

from ansible_collections.glitchwrks.fastiron.plugins.plugin_utils.fastiron_ssh_keys import (
    MAX_KEYS,
    FastIronKeyError,
    parse_client_pub_keys,
    parse_openssh_key,
    to_rfc4716,
)

# The name the switch's SCP server uses for the client public key list.
KEY_FILE = 'sshPubKey'


class ActionModule(ActionBase):

    _VALID_ARGS = frozenset(('keys',))
    _supports_check_mode = True

    def run(self, tmp=None, task_vars=None):
        result = super().run(tmp, task_vars)
        del tmp

        dummy, args = self.validate_argument_spec(argument_spec=dict(
            keys=dict(type='list', elements='str', required=True, no_log=False),
        ))

        if self._play_context.connection.split('.')[-1] != 'network_cli':
            result.update(failed=True, msg='this action requires the ansible.netcommon.network_cli connection')
            return result

        wanted = []
        for key in args['keys']:
            try:
                dummy, body = parse_openssh_key(key)
            except FastIronKeyError as e:
                result.update(failed=True, msg=to_text(e))
                return result
            if body not in wanted:
                wanted.append(body)
        if not wanted:
            result.update(failed=True, msg='at least one key is required')
            return result
        if len(wanted) > MAX_KEYS:
            result.update(failed=True, msg='FastIron holds at most %d keys, got %d' % (MAX_KEYS, len(wanted)))
            return result

        conn = Connection(self._connection.socket_path)
        try:
            current = parse_client_pub_keys(conn.get('show ip client-pub-key'))
            changed = set(current) != set(wanted)

            if self._task.diff:
                result['diff'] = dict(
                    before=to_rfc4716(current) if current else '',
                    after=to_rfc4716(wanted),
                    before_header=KEY_FILE,
                    after_header=KEY_FILE,
                )

            if changed and not self._task.check_mode:
                fd, path = tempfile.mkstemp(suffix='.sshPubKey')
                try:
                    with os.fdopen(fd, 'w') as f:
                        f.write(to_rfc4716(wanted))
                    self._upload(path, conn.get_option('persistent_command_timeout'))
                finally:
                    os.remove(path)

                after = parse_client_pub_keys(conn.get('show ip client-pub-key'))
                if set(after) != set(wanted):
                    result.update(failed=True, changed=True,
                                  msg='the switch does not list the uploaded keys')
                    return result
        except ConnectionError as e:
            result.update(failed=True, msg=to_text(e))
            return result

        result['changed'] = changed
        return result

    def _upload(self, path, timeout):
        """Copy the key file to the switch over a separate SSH connection.

        FastIron allows one channel per SSH connection, and the CLI shell of the
        persistent connection holds it. In this process, the network_cli connection
        has no SSH session of its own yet, so copy_file opens a new one, with the same
        credentials and options, and it is closed afterwards. (net_put cannot be used:
        it first tries to download the destination, and when that fails
        ansible.netcommon's libssh connection drops its session.)
        """
        try:
            self._connection.copy_file(source=path, destination=KEY_FILE, proto='scp', timeout=timeout)
        finally:
            ssh = self._connection.ssh_type_conn
            # Only libssh caches the session; it has none when connecting failed.
            if self._connection.ssh_type == 'libssh' and hasattr(ssh, 'ssh'):
                ssh.close()
