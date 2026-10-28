# -*- coding: utf-8 -*-
# Copyright: (c) 2026, Glitch Works, LLC
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

DOCUMENTATION = r'''
module: fastiron_ssh_keys
author: Jonathan Chapman (@glitchwrks)
short_description: Manage the SSH client public keys of a FastIron switch
version_added: 0.1.0
description:
  - Sets the list of public keys a FastIron switch accepts for SSH logins to exactly the given keys,
    uploading a new list over SCP when it differs from the current one.
  - FastIron keeps this list outside the configuration. It does not appear in C(show running-config),
    is kept across a reload without C(write memory), and each upload replaces the whole list.
  - The keys are not tied to any user. Every listed key logs in as an anonymous super-user, and the
    username given by the client is ignored. Treat the list like the C(authorized_keys) of root.
  - Make sure the key used by the current connection stays in O(keys), or later connections with it
    will fail.
extends_documentation_fragment:
  - ansible.builtin.action_common_attributes
  - ansible.builtin.action_common_attributes.flow
options:
  keys:
    description:
      - Public keys in OpenSSH format, as found in C(.pub) files and C(authorized_keys).
      - FastIron accepts RSA keys of up to 2048 bits and DSA keys, up to 16 in all. Other key types,
        such as Ed25519, fail.
      - Duplicates are ignored. The comment of each key is not uploaded.
    type: list
    elements: str
    required: true
attributes:
  action:
    support: full
  async:
    support: none
  bypass_host_loop:
    support: none
  check_mode:
    support: full
  diff_mode:
    support: full
  platform:
    platforms: fastiron
notes:
  - Requires the P(ansible.netcommon.network_cli#connection) connection with C(ansible_network_os)
    set to C(glitchwrks.fastiron.fastiron) and C(ansible_network_cli_ssh_type) set to C(libssh), and
    SCP enabled on the switch, which is the default.
  - FastIron allows one channel per SSH connection, which the CLI session uses. Uploads open a
    second SSH connection with the same credentials and options, closed again afterwards, so it
    counts against the switch's limit of concurrent SSH sessions while it is open.
'''

EXAMPLES = r'''
- name: Allow only the control node's key
  glitchwrks.fastiron.fastiron_ssh_keys:
    keys:
      - "{{ lookup('ansible.builtin.file', '~/.ssh/id_rsa_fastiron.pub') }}"
'''

RETURN = r''' # '''
