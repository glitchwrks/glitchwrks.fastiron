# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os

import pytest

from ansible_collections.glitchwrks.fastiron.plugins.plugin_utils.fastiron_ssh_keys import (
    FastIronKeyError,
    parse_client_pub_keys,
    parse_openssh_key,
    scp_send,
    to_rfc4716,
)

FIXTURES = os.path.join(os.path.dirname(__file__), '..', '..', 'fixtures')


def key(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read().strip()


@pytest.mark.parametrize('name, key_type', [('rsa2048.pub', 'ssh-rsa'), ('dsa.pub', 'ssh-dss')])
def test_accepts_supported_keys(name, key_type):
    line = key(name)
    assert parse_openssh_key(line) == (key_type, line.split()[1])


@pytest.mark.parametrize('name, message', [
    ('rsa4096.pub', 'over 2048 bits, this one has 4096'),
    ('ed25519.pub', 'does not accept ssh-ed25519 keys'),
])
def test_rejects_keys_the_switch_cannot_use(name, message):
    with pytest.raises(FastIronKeyError, match=message):
        parse_openssh_key(key(name))


@pytest.mark.parametrize('line', [
    'ssh-rsa',
    'ssh-rsa not*base64',
    # Valid base64, but the embedded type says ed25519.
    'ssh-rsa ' + key('ed25519.pub').split()[1],
])
def test_rejects_malformed_keys(line):
    with pytest.raises(FastIronKeyError):
        parse_openssh_key(line)


def test_rfc4716_round_trip():
    bodies = [key('rsa2048.pub').split()[1], key('dsa.pub').split()[1]]
    text = to_rfc4716(bodies)
    assert all(len(line) <= 70 for line in text.splitlines())
    assert parse_client_pub_keys(text) == bodies


def test_parses_switch_output_with_headers_and_prompt_noise():
    body = key('rsa2048.pub').split()[1]
    wrapped = '\n'.join(body[i:i + 64] for i in range(0, len(body), 64))
    output = ('---- BEGIN SSH2 PUBLIC KEY ----\n'
              'Comment: "2048-bit RSA, converted by admin@host from \\\n'
              'OpenSSH"\n'
              '%s\n'
              '---- END SSH2 PUBLIC KEY ----\n' % wrapped)
    assert parse_client_pub_keys(output) == [body]


def test_parses_output_captured_from_an_icx6650():
    # Layout of "show ip client-pub-key" on FastIron 08.0.30, with a test key: the
    # switch keeps the comment ssh-keygen added, cut short, and 70-column lines.
    body = key('rsa2048.pub').split()[1]
    wrapped = '\n'.join(body[i:i + 70] for i in range(0, len(body), 70))
    output = ('---- BEGIN SSH2 PUBLIC KEY ----\n'
              'Comment: "2048-bit RSA, converted by admin@host.example.net from Op"\n'
              '%s\n'
              '---- END SSH2 PUBLIC KEY ----\n'
              '\n' % wrapped)
    assert parse_client_pub_keys(output) == [body]


def test_no_keys():
    assert parse_client_pub_keys('') == []


class ScriptedChannel:
    """A channel that replies to each SCP step from a script."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.pending = []
        self.command = None
        self.written = b''
        self.eof_sent = False

    def request_exec(self, command):
        self.command = command
        self._next()

    def write(self, data):
        self.written += data
        if data.endswith((b'\0', b'\n')):
            self._next()
        return len(data)

    def _next(self):
        if self.replies:
            self.pending.append(self.replies.pop(0))

    def poll(self, timeout=-1):
        return len(self.pending[0]) if self.pending else 0

    @property
    def is_eof(self):
        return not self.pending

    def recv(self, size=1024):
        return self.pending.pop(0)

    def send_eof(self):
        self.eof_sent = True


def test_scp_send_speaks_the_protocol_unquoted():
    channel = ScriptedChannel([b'\0', b'\0', b'\0'])
    assert scp_send(channel, 'sshPubKey', b'keys\n') is True
    # libssh would send "scp -t 'sshPubKey'", which FastIron refuses.
    assert channel.command == 'scp -t sshPubKey'
    assert channel.written == b'C0644 5 sshPubKey\nkeys\n\0'
    assert channel.eof_sent


@pytest.mark.parametrize('replies, message', [
    ([b'\0', b'\x01Wrong file name is specified\n'],
     'refused the key file before receiving it: Wrong file name is specified'),
    ([b'\x02fatal\n'], 'refused the key file starting the upload: fatal'),
    ([b'\0', b'garbage'], 'unexpected reply'),
    ([], 'closed the SCP connection starting the upload'),
    ([b'\0'], 'closed the SCP connection before receiving it'),
])
def test_scp_send_reports_what_the_switch_says(replies, message):
    with pytest.raises(FastIronKeyError, match=message):
        scp_send(ScriptedChannel(replies), 'sshPubKey', b'keys\n', timeout=1)


def test_scp_send_accepts_a_disconnect_once_the_file_is_sent():
    # FastIron may drop the connection instead of acknowledging the file; the caller
    # then checks the key list.
    channel = ScriptedChannel([b'\0', b'\0'])
    assert scp_send(channel, 'sshPubKey', b'keys\n') is False
    assert channel.written.endswith(b'keys\n\0')


def test_scp_send_reports_a_dropped_connection_before_the_file():
    class Dropped(ScriptedChannel):
        def poll(self, timeout=-1):
            if self.command and self.written:
                raise RuntimeError('Socket error: disconnected')
            return super().poll(timeout)

    with pytest.raises(FastIronKeyError, match='before receiving it: Socket error: disconnected'):
        scp_send(Dropped([b'\0']), 'sshPubKey', b'keys\n', timeout=1)
