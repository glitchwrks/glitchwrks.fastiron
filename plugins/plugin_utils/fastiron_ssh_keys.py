# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert between OpenSSH public keys and the FastIron client public key list.

FastIron keeps one list of client public keys, in the SSH2 (RFC 4716) format,
outside the configuration. Runs on the controller.
"""

from __future__ import annotations

import base64
import binascii
import re
import struct
import time

# FastIron 08.0.30 accepts RSA keys of up to 2048 bits, and DSA keys.
SUPPORTED_TYPES = ('ssh-rsa', 'ssh-dss')
MAX_RSA_BITS = 2048
# The switch holds at most 16 keys.
MAX_KEYS = 16

_BLOCK_RE = re.compile(r'---- BEGIN SSH2 PUBLIC KEY ----(.*?)---- END SSH2 PUBLIC KEY ----', re.S)
_HEADER_RE = re.compile(r'^[\w-]+:')


class FastIronKeyError(Exception):
    pass


def _strings(blob):
    """Split an SSH wire-format key blob into its length-prefixed fields."""
    fields = []
    offset = 0
    while offset < len(blob):
        if offset + 4 > len(blob):
            raise FastIronKeyError('truncated key data')
        length = struct.unpack('>I', blob[offset:offset + 4])[0]
        offset += 4
        if offset + length > len(blob):
            raise FastIronKeyError('truncated key data')
        fields.append(blob[offset:offset + length])
        offset += length
    return fields


def parse_openssh_key(line):
    """Return (type, base64 body) for an OpenSSH public key line.

    Rejects key types and sizes the switch does not accept.
    """
    parts = line.split()
    if len(parts) < 2:
        raise FastIronKeyError('not an OpenSSH public key: %r' % line)
    key_type, body = parts[0], parts[1]
    try:
        blob = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError):
        raise FastIronKeyError('key data is not valid base64: %r' % line)
    fields = _strings(blob)
    if not fields or fields[0].decode('ascii', 'replace') != key_type:
        raise FastIronKeyError('key data does not match the key type %s' % key_type)
    if key_type not in SUPPORTED_TYPES:
        raise FastIronKeyError('FastIron does not accept %s keys, only %s'
                               % (key_type, ', '.join(SUPPORTED_TYPES)))
    if key_type == 'ssh-rsa':
        if len(fields) != 3:
            raise FastIronKeyError('malformed ssh-rsa key')
        bits = int.from_bytes(fields[2], 'big').bit_length()
        if bits > MAX_RSA_BITS:
            raise FastIronKeyError('FastIron does not accept RSA keys over %d bits, this one has %d'
                                   % (MAX_RSA_BITS, bits))
    return key_type, body


def parse_client_pub_keys(text):
    """Return the base64 bodies of the keys in "show ip client-pub-key" output."""
    keys = []
    for block in _BLOCK_RE.findall(text):
        body = []
        continued = False
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            # Header lines ("Comment: ...") may continue with a trailing backslash.
            if continued or _HEADER_RE.match(line):
                continued = line.endswith('\\')
                continue
            body.append(line)
        keys.append(''.join(body))
    return keys


def to_rfc4716(bodies):
    """Render key bodies as a FastIron client public key file."""
    lines = []
    for body in bodies:
        lines.append('---- BEGIN SSH2 PUBLIC KEY ----')
        lines.extend(body[i:i + 70] for i in range(0, len(body), 70))
        lines.append('---- END SSH2 PUBLIC KEY ----')
    return '\n'.join(lines) + '\n'


class _ScpDisconnected(Exception):
    pass


def _scp_ack(channel, timeout, step):
    """Wait for the SCP server to accept a step, or raise with its message."""
    deadline = time.monotonic() + timeout
    reply = b''
    while True:
        if reply[:1] == b'\0':
            return
        if reply[:1] in (b'\x01', b'\x02') and b'\n' in reply:
            message = reply[1:].split(b'\n', 1)[0].decode('utf-8', 'replace').strip()
            raise FastIronKeyError('the switch refused the key file %s: %s' % (step, message))
        if reply and reply[:1] not in (b'\x01', b'\x02'):
            raise FastIronKeyError('unexpected reply from the switch\'s SCP server %s: %r' % (step, reply[:80]))
        if time.monotonic() > deadline:
            raise FastIronKeyError('timed out waiting for the switch\'s SCP server %s' % step)
        # pylibssh raises its own exceptions when the connection drops.
        try:
            ready = channel.poll(timeout=100)
            closed = ready < 0 or (ready == 0 and channel.is_eof)
            chunk = channel.recv(1024) if ready > 0 else b''
        except Exception as e:
            raise _ScpDisconnected(str(e))
        if closed or chunk is None:
            raise _ScpDisconnected('the switch closed the SCP connection')
        reply += chunk


def scp_send(channel, name, data, timeout=30):
    """Upload data as name with the SCP protocol, on an open pylibssh channel.

    libssh shell-quotes the target of "scp -t", which FastIron does not unquote: it
    accepts the request, then refuses the file with "Wrong file name is specified".
    Send the request the way OpenSSH's scp does, unquoted.

    FastIron may drop the connection once it has the file, instead of acknowledging
    it. Return True when the switch acknowledged the file, and False when it
    disconnected after receiving it; the caller then has to check that the switch
    took it.
    """
    steps = (
        ('starting the upload', None),
        ('before receiving it', b'C0644 %d %s\n' % (len(data), name.encode('ascii'))),
        ('after receiving it', data + b'\0'),
    )
    channel.request_exec('scp -t %s' % name)
    for index, (step, payload) in enumerate(steps):
        if payload is not None:
            channel.write(payload)
        try:
            _scp_ack(channel, timeout, step)
        except _ScpDisconnected as e:
            if index == len(steps) - 1:
                return False
            raise FastIronKeyError('the switch closed the SCP connection %s: %s' % (step, e))
    channel.send_eof()
    return True
