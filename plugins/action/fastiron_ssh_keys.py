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
                # Upload over the connection's existing SSH session. net_put first tries to
                # download the destination, and ansible.netcommon's libssh connection drops
                # its session when that fails, which closes the CLI shell along with it.
                fd, path = tempfile.mkstemp(suffix='.sshPubKey')
                try:
                    with os.fdopen(fd, 'w') as f:
                        f.write(to_rfc4716(wanted))
                    conn.copy_file(source=path, destination=KEY_FILE, proto='scp',
                                   timeout=conn.get_option('persistent_command_timeout'))
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
