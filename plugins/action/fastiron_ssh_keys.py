# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import time

from ansible.module_utils.common.text.converters import to_text
from ansible.module_utils.connection import Connection, ConnectionError
from ansible.plugins.action import ActionBase

from ansible_collections.glitchwrks.fastiron.plugins.plugin_utils.fastiron_ssh_keys import (
    MAX_KEYS,
    FastIronKeyError,
    parse_client_pub_keys,
    parse_openssh_key,
    scp_send,
    to_rfc4716,
)

# The name the switch's SCP server uses for the client public key list.
KEY_FILE = 'sshPubKey'

# Some FastIron builds (ICX 6610, 08.0.30uT7f3) refuse a new SSH connection for
# about 13 seconds after the last one, by dropping it during key exchange. The
# upload connection follows the persistent connection closely, so it is retried
# for a while when that happens.
RETRY_SECONDS = 30
RETRY_INTERVAL = 2
_REFUSED = 'Socket error: disconnected'


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
                self._upload(to_rfc4716(wanted).encode('ascii'), conn.get_option('persistent_command_timeout'))

                after = parse_client_pub_keys(conn.get('show ip client-pub-key'))
                if set(after) != set(wanted):
                    result.update(failed=True, changed=True,
                                  msg='the switch does not list the uploaded keys')
                    return result
        except ConnectionError as e:
            result.update(failed=True, msg=to_text(e))
            return result
        except FastIronKeyError as e:
            result.update(failed=True, msg=to_text(e))
            return result

        result['changed'] = changed
        return result

    def _upload(self, data, timeout):
        """Send the key file to the switch over a separate SSH connection.

        FastIron allows one channel per SSH connection, and the CLI shell of the
        persistent connection holds it. In this process, the network_cli connection's
        libssh connection has no SSH session yet, so connecting opens a new one with
        the same credentials and options, and it is closed afterwards.

        The SCP protocol is spoken directly instead of with libssh's SCP support or
        net_put, which FastIron rejects (see scp_send), and which drops the session
        after a failed download, respectively.
        """
        if self._connection.ssh_type != 'libssh':
            raise FastIronKeyError('uploading keys requires ansible_network_cli_ssh_type set to libssh')
        ssh = self._connection.ssh_type_conn
        try:
            self._connect(ssh)
            channel = ssh.ssh.new_channel()
            try:
                if not scp_send(channel, KEY_FILE, data, timeout):
                    # The caller checks the key list the switch shows afterwards.
                    self._display.vvv('the switch disconnected after receiving the key file')
            finally:
                channel.close()
        finally:
            # A connection that failed has no session to close.
            if hasattr(ssh, 'ssh'):
                ssh.close()

    def _connect(self, ssh):
        """Connect, retrying while the switch refuses new connections."""
        deadline = time.monotonic() + RETRY_SECONDS
        while True:
            try:
                ssh._connect()
                return
            except Exception as e:
                if _REFUSED not in to_text(e) or time.monotonic() + RETRY_INTERVAL > deadline:
                    raise
                self._display.vvv('the switch refused the upload connection, retrying in %ds' % RETRY_INTERVAL)
                time.sleep(RETRY_INTERVAL)
