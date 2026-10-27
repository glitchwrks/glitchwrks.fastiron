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
