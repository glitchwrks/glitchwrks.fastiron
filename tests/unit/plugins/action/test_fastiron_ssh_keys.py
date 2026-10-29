# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from ansible.errors import AnsibleError
from ansible.playbook.task import Task

from ansible_collections.glitchwrks.fastiron.plugins.action.fastiron_ssh_keys import ActionModule
from ansible_collections.glitchwrks.fastiron.plugins.plugin_utils.fastiron_ssh_keys import to_rfc4716

FIXTURES = os.path.join(os.path.dirname(__file__), '..', '..', 'fixtures')


def key(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read().strip()


RSA = key('rsa2048.pub')
DSA = key('dsa.pub')


class FakeScpChannel:
    """An SCP sink on its own SSH channel, as FastIron's: one file, named sshPubKey."""

    def __init__(self, switch):
        self.switch = switch
        self.command = None
        self.received = b''
        self.replies = []
        self.closed = False

    def request_exec(self, command):
        self.command = command
        self.replies.append(b'\0')

    def poll(self, timeout=-1):
        return len(self.replies[0]) if self.replies else 0

    @property
    def is_eof(self):
        return not self.replies

    def recv(self, size=1024):
        return self.replies.pop(0)

    def write(self, data):
        self.received += data
        if self.received.endswith(b'\n') and self.received.count(b'\n') == 1:
            self.replies.append(b'\0' if self.command == 'scp -t sshPubKey'
                                else b'\x01Wrong file name is specified\n')
        elif self.received.endswith(b'\0'):
            content = self.received.split(b'\n', 1)[1][:-1].decode()
            self.switch.uploads.append((self.command, content))
            if self.switch.accept_uploads:
                self.switch.listing = content
            self.replies.append(b'\0')
        return len(data)

    def send_eof(self):
        pass

    def close(self):
        self.closed = True


class FakeSwitch:
    """Stands in for the switch: its key list, read over the persistent connection's
    CLI session, and SCP uploads, made over a separate SSH connection."""

    def __init__(self):
        self.listing = ''
        self.uploads = []
        self.accept_uploads = True
        self.channels = []
        # The libssh connection used for uploads; its session is set by _connect().
        self.ssh = MagicMock(spec=['_connect', 'close'])
        self.ssh._connect.side_effect = self.connect

    def connect(self):
        self.ssh.ssh = MagicMock()
        self.ssh.ssh.new_channel.side_effect = self.new_channel

    def new_channel(self):
        channel = FakeScpChannel(self)
        self.channels.append(channel)
        return channel

    def get(self, command):
        assert command == 'show ip client-pub-key'
        return self.listing

    def get_option(self, name):
        return 30


@pytest.fixture
def switch(mocker):
    fake = FakeSwitch()
    mocker.patch('ansible_collections.glitchwrks.fastiron.plugins.action.fastiron_ssh_keys.Connection',
                 return_value=fake)
    return fake


def run(switch, keys, check_mode=False, diff=False, connection='ansible.netcommon.network_cli'):
    task = Task()
    task.action = 'glitchwrks.fastiron.fastiron_ssh_keys'
    task.args = {'keys': keys}
    task.check_mode = check_mode
    task.diff = diff
    play_context = MagicMock(connection=connection, check_mode=check_mode)
    task_connection = MagicMock(socket_path='/dev/null', ssh_type='libssh', ssh_type_conn=switch.ssh)
    action = ActionModule(task, task_connection, play_context,
                          loader=None, templar=None, shared_loader_obj=None)
    return action.run(task_vars={})


def test_uploads_when_the_list_differs(switch):
    switch.listing = to_rfc4716([DSA.split()[1]])
    result = run(switch, [RSA])
    assert result['changed'] is True
    assert not result.get('failed')
    assert switch.uploads == [('scp -t sshPubKey', to_rfc4716([RSA.split()[1]]))]
    # The separate SSH connection used for the upload is closed again.
    assert [c.closed for c in switch.channels] == [True]
    switch.ssh.close.assert_called_once_with()


def test_no_upload_when_the_list_matches_in_any_order(switch):
    switch.listing = to_rfc4716([DSA.split()[1], RSA.split()[1]])
    result = run(switch, [RSA, DSA, RSA])
    assert result['changed'] is False
    assert switch.uploads == []
    switch.ssh.close.assert_not_called()


def test_failed_upload_connection_is_reported(switch):
    switch.ssh._connect.side_effect = AnsibleError('ssh connect failed')
    with pytest.raises(AnsibleError, match='ssh connect failed'):
        run(switch, [RSA])
    # A libssh connection that never connected has no session to close.
    switch.ssh.close.assert_not_called()


@pytest.fixture
def clock(mocker):
    """A clock that only moves when the action sleeps."""
    now = [0.0]
    module = 'ansible_collections.glitchwrks.fastiron.plugins.action.fastiron_ssh_keys.time'
    mocker.patch(module + '.monotonic', side_effect=lambda: now[0])
    mocker.patch(module + '.sleep', side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds))
    return now


def test_upload_connection_is_retried_while_the_switch_refuses_it(switch, clock):
    refusals = [AnsibleError('ssh connection failed: ssh connect failed: Socket error: disconnected')] * 6

    def connect():
        if refusals:
            raise refusals.pop(0)
        switch.connect()
    switch.ssh._connect.side_effect = connect
    result = run(switch, [RSA])
    assert result['changed'] is True and not result.get('failed')
    assert switch.ssh._connect.call_count == 7
    assert clock[0] == 12
    assert len(switch.uploads) == 1


def test_upload_connection_gives_up_after_the_retry_window(switch, clock):
    switch.ssh._connect.side_effect = AnsibleError('ssh connect failed: Socket error: disconnected')
    with pytest.raises(AnsibleError, match='disconnected'):
        run(switch, [RSA])
    assert clock[0] <= 30
    assert switch.ssh._connect.call_count == 16  # at 0, 2, ... 30 seconds


def test_other_connection_failures_are_not_retried(switch, clock):
    switch.ssh._connect.side_effect = AnsibleError('ssh connect failed: Access denied')
    with pytest.raises(AnsibleError, match='Access denied'):
        run(switch, [RSA])
    assert switch.ssh._connect.call_count == 1
    assert clock[0] == 0


def test_check_mode_reports_the_change_without_uploading(switch):
    result = run(switch, [RSA], check_mode=True, diff=True)
    assert result['changed'] is True
    assert switch.uploads == []
    assert result['diff']['before'] == ''
    assert result['diff']['after'] == to_rfc4716([RSA.split()[1]])


def test_fails_when_the_switch_ignores_the_upload(switch):
    switch.accept_uploads = False
    result = run(switch, [RSA])
    assert result['failed'] is True
    assert 'does not list the uploaded keys' in result['msg']


@pytest.mark.parametrize('keys, message', [
    ([key('ed25519.pub')], 'does not accept ssh-ed25519 keys'),
    ([key('rsa4096.pub')], 'over 2048 bits'),
])
def test_rejects_unusable_keys_before_touching_the_switch(switch, keys, message):
    result = run(switch, keys)
    assert result['failed'] is True
    assert message in result['msg']
    assert switch.uploads == []


def test_requires_libssh_for_uploads(switch, mocker):
    task_connection = MagicMock(socket_path='/dev/null', ssh_type='paramiko')
    task = Task()
    task.action = 'glitchwrks.fastiron.fastiron_ssh_keys'
    task.args = {'keys': [RSA]}
    action = ActionModule(task, task_connection, MagicMock(connection='ansible.netcommon.network_cli'),
                          loader=None, templar=None, shared_loader_obj=None)
    result = action.run(task_vars={})
    assert result['failed'] is True
    assert 'libssh' in result['msg']
    assert switch.uploads == []


def test_requires_network_cli(switch):
    result = run(switch, [RSA], connection='ansible.builtin.ssh')
    assert result['failed'] is True
    assert switch.uploads == []
