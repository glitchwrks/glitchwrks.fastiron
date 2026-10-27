# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from ansible.playbook.task import Task

from ansible_collections.glitchwrks.fastiron.plugins.action.fastiron_ssh_keys import ActionModule
from ansible_collections.glitchwrks.fastiron.plugins.plugin_utils.fastiron_ssh_keys import to_rfc4716

FIXTURES = os.path.join(os.path.dirname(__file__), '..', '..', 'fixtures')


def key(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read().strip()


RSA = key('rsa2048.pub')
DSA = key('dsa.pub')


class FakeSwitch:
    """Stands in for the persistent connection: a key list and an SCP upload."""

    def __init__(self, keys=()):
        self.listing = to_rfc4716([k.split()[1] for k in keys]) if keys else ''
        self.uploads = []
        self.accept_uploads = True

    def get(self, command):
        assert command == 'show ip client-pub-key'
        return self.listing

    def get_option(self, name):
        return 30

    def copy_file(self, source, destination, proto, timeout):
        with open(source) as f:
            content = f.read()
        self.uploads.append((destination, proto, content))
        if self.accept_uploads:
            self.listing = content


@pytest.fixture
def switch(mocker):
    fake = FakeSwitch()
    mocker.patch('ansible_collections.glitchwrks.fastiron.plugins.action.fastiron_ssh_keys.Connection',
                 return_value=fake)
    return fake


def run(keys, check_mode=False, diff=False, connection='ansible.netcommon.network_cli'):
    task = Task()
    task.action = 'glitchwrks.fastiron.fastiron_ssh_keys'
    task.args = {'keys': keys}
    task.check_mode = check_mode
    task.diff = diff
    play_context = MagicMock(connection=connection, check_mode=check_mode)
    action = ActionModule(task, MagicMock(socket_path='/dev/null'), play_context,
                          loader=None, templar=None, shared_loader_obj=None)
    return action.run(task_vars={})


def test_uploads_when_the_list_differs(switch):
    switch.listing = to_rfc4716([DSA.split()[1]])
    result = run([RSA])
    assert result['changed'] is True
    assert not result.get('failed')
    assert switch.uploads == [('sshPubKey', 'scp', to_rfc4716([RSA.split()[1]]))]


def test_no_upload_when_the_list_matches_in_any_order(switch):
    switch.listing = to_rfc4716([DSA.split()[1], RSA.split()[1]])
    result = run([RSA, DSA, RSA])
    assert result['changed'] is False
    assert switch.uploads == []


def test_check_mode_reports_the_change_without_uploading(switch):
    result = run([RSA], check_mode=True, diff=True)
    assert result['changed'] is True
    assert switch.uploads == []
    assert result['diff']['before'] == ''
    assert result['diff']['after'] == to_rfc4716([RSA.split()[1]])


def test_fails_when_the_switch_ignores_the_upload(switch):
    switch.accept_uploads = False
    result = run([RSA])
    assert result['failed'] is True
    assert 'does not list the uploaded keys' in result['msg']


@pytest.mark.parametrize('keys, message', [
    ([key('ed25519.pub')], 'does not accept ssh-ed25519 keys'),
    ([key('rsa4096.pub')], 'over 2048 bits'),
])
def test_rejects_unusable_keys_before_touching_the_switch(switch, keys, message):
    result = run(keys)
    assert result['failed'] is True
    assert message in result['msg']
    assert switch.uploads == []


def test_requires_network_cli(switch):
    result = run([RSA], connection='ansible.builtin.ssh')
    assert result['failed'] is True
    assert switch.uploads == []
