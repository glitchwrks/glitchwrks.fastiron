# glitchwrks.fastiron

Ansible support for Brocade / Ruckus FastIron switches over the CLI, with checks for
Multi-Chassis Trunking (MCT) configuration mistakes before a failover exposes them.

Maintained by [Glitch Works, LLC](https://www.glitchwrks.com/). Developed and tested against
ICX 6650 switches running FastIron 08.0.30. Other FastIron switches and releases with the same
CLI are likely to work, but have not been tested.

## Contents

| Name | Type | Description |
| --- | --- | --- |
| `glitchwrks.fastiron.fastiron` | cliconf | Reads and changes configuration for `ansible.netcommon.cli_command` and `ansible.netcommon.cli_config` |
| `glitchwrks.fastiron.fastiron` | terminal | Recognizes FastIron prompts and errors, turns off paging, and enters privileged EXEC mode |
| `glitchwrks.fastiron.fastiron_ssh_keys` | module | Sets the list of public keys the switch accepts for SSH logins |
| `glitchwrks.fastiron.fastiron_parse_config` | filter | Parses a running configuration into VLANs, LAGs, VE addresses and MCT clusters |
| `glitchwrks.fastiron.fastiron_mct_audit` | filter | Checks one switch's MCT clusters against the Ruckus MCT design rules |
| `glitchwrks.fastiron.fastiron_mct_compare` | filter | Checks that two MCT peers agree |
| `glitchwrks.fastiron.fastiron_model_config` | filter | Validates a model of a switch's VLANs, LAGs, VE addresses and MCT clusters |
| `glitchwrks.fastiron.fastiron_config_drift` | filter | Reports how a running configuration differs from a model |
| `glitchwrks.fastiron.fastiron_config_commands` | filter | Generates the commands that add what a switch is missing from a model |
| `glitchwrks.fastiron.fastiron_parse_cluster_status` | filter | Parses the live state of MCT clusters from `show cluster` |
| `glitchwrks.fastiron.fastiron_mct_health` | filter | Checks `show cluster` for MCT clusters, peers and clients that aren't working |
| `glitchwrks.fastiron.fastiron_mct_health_compare` | filter | Checks both MCT peers' `show cluster` together, for split-brain |

Full documentation for each is available with `ansible-doc`, for example
`ansible-doc -t filter glitchwrks.fastiron.fastiron_mct_audit`.

## Requirements

- ansible-core 2.19 or later on the controller
- [`ansible.netcommon`](https://galaxy.ansible.com/ui/repo/published/ansible/netcommon/) 8.6.0 or
  later, installed automatically as a dependency. Older releases, such as the one included in the
  `ansible` 13 package, use a feature that ansible-core 2.23 removes
- [`ansible-pylibssh`](https://pypi.org/project/ansible-pylibssh/) 1.4.0 or later, installed in
  the Python environment Ansible runs from. It is required: the switches only offer older SSH
  algorithms (see below), which Ansible reaches through libssh, and `ansible-galaxy` does not
  install Python packages. It is listed in [`requirements.txt`](requirements.txt).

## Installation

```sh
ansible-galaxy collection install glitchwrks.fastiron
pip install 'ansible-pylibssh>=1.4.0'
```

Or straight from GitHub:

```sh
ansible-galaxy collection install git+https://github.com/glitchwrks/glitchwrks.fastiron.git
```

With [uv](https://docs.astral.sh/uv/), add `ansible-pylibssh` to the project that runs Ansible
instead of using pip: `uv add 'ansible-pylibssh>=1.4.0'`.

## Connecting to the switches

FastIron 08.0.30 only offers SSH algorithms that libssh turns off by default:
the `diffie-hellman-group1-sha1` key exchange, `ssh-rsa` host keys, the `aes256-cbc` cipher and the
`hmac-sha1` MAC.
`ansible.netcommon.network_cli` can use them through libssh, but needs some of them set in two
different places.

The key exchange and host key algorithms are set as inventory variables. The cipher and MAC have
no `network_cli` option, but libssh reads the controller user's `~/.ssh/config`, so add a `Host`
block for the switches there:

```
# ~/.ssh/config on the Ansible controller
Host cs1a.example.net cs1b.example.net
    KexAlgorithms +diffie-hellman-group1-sha1
    HostKeyAlgorithms +ssh-rsa
    Ciphers +aes256-cbc
    MACs +hmac-sha1
```

These algorithms are weak. Only reach the switches' management interfaces over a trusted
network or a VPN.

Then set the connection up in the inventory:

```yaml
# group_vars/fastiron.yml
ansible_connection: ansible.netcommon.network_cli
ansible_network_os: glitchwrks.fastiron.fastiron
ansible_network_cli_ssh_type: libssh
ansible_libssh_key_exchange_algorithms: +diffie-hellman-group1-sha1
ansible_libssh_hostkeys: +ssh-rsa
ansible_become: true
ansible_become_method: ansible.netcommon.enable
ansible_become_password: "{{ vault_enable_password }}"
```

Sessions log in to user EXEC mode (`>`). With `ansible_become`, the terminal plugin runs `enable`
and answers the password prompt. When no enable password is set on the switch, `enable` succeeds
without one.

Without `ansible_become`, commands run in user EXEC mode, where FastIron pages long output and does
not allow turning paging off. The cliconf plugin answers the pager and removes it from the output,
so `show running-config` returns the same output as in privileged EXEC mode. That is enough for
read-only uses such as configuration snapshots; changing the configuration needs privileged EXEC
mode.

### Logging in with SSH keys

FastIron handles SSH keys differently from OpenSSH:

- The switch keeps a single list of public keys, not one per user. Any listed key logs in as an
  anonymous super-user, whatever username the client gives, and `show who` names no user for the
  session. Treat the list like root's `authorized_keys`, and keep the enable password set.
- FastIron 08.0.30 accepts RSA keys of up to 2048 bits and DSA keys. Ed25519 and larger RSA keys
  are not accepted, so the control node usually needs a separate key for the switches:
  ```sh
  ssh-keygen -t rsa -b 2048 -f ~/.ssh/id_rsa_fastiron
  ```
- The list is kept outside the configuration. Each upload replaces the whole list, and it is kept
  across a reload without `write memory`.

Set the list with `glitchwrks.fastiron.fastiron_ssh_keys`, and point the connection at the key.
libssh only offers `ssh-rsa` signatures when asked to:

```yaml
# group_vars/fastiron.yml
ansible_private_key_file: ~/.ssh/id_rsa_fastiron
ansible_libssh_publickey_algorithms: +ssh-rsa
```

Turning password logins off with `ip ssh password-authentication no` applies to every user, so
local accounts can then only log in on the console.

## Usage

### Running commands

```yaml
- name: Show the MCT cluster state
  ansible.netcommon.cli_command:
    command: show cluster
```

`write memory` returns only once the copy to flash has finished, including its
`Flash to Flash Done.` line.

### Backing up and changing configuration

```yaml
- name: Back up the running configuration
  ansible.netcommon.cli_config:
    backup: true
    backup_options:
      dir_path: "{{ playbook_dir }}/backups"
      filename: "{{ inventory_hostname }}.cfg"

- name: Make sure a VLAN exists on the ICL
  ansible.netcommon.cli_config:
    config: |
      vlan 2000 name Lab by port
       tagged ethe 1/1/7 to 1/1/8
```

The running configuration can contain secrets, such as SNMP communities, and password hashes when
the switch is set to display them. Encrypt backups, for example with `ansible-vault`, before
committing them anywhere.

`cli_config` compares the candidate with the running configuration on the controller and only
sends the lines that are missing, stepping in and out of configuration sub-modes as needed. In
check mode it reports what would change without changing anything. Write the candidate the way
it appears in `show running-config`, nested blocks indented by one space, so that lines
already present are recognized. Replacing the whole configuration is not supported. Changes are
not saved to flash until `write memory`.

### Checking MCT configuration

```yaml
- name: Get the running configuration
  ansible.netcommon.cli_command:
    command: show running-config
  register: running

- name: Check this switch's MCT configuration
  vars:
    findings: "{{ running.stdout | glitchwrks.fastiron.fastiron_mct_audit }}"
  ansible.builtin.assert:
    that: findings | selectattr('severity', '==', 'error') | list | length == 0
    fail_msg: "{{ findings | map(attribute='message') }}"

- name: Check that the MCT peers agree
  vars:
    findings: >-
      {{ running.stdout | glitchwrks.fastiron.fastiron_mct_compare(
           hostvars[mct_peer].running.stdout, name=inventory_hostname, peer_name=mct_peer) }}
  ansible.builtin.assert:
    that: findings | length == 0
    fail_msg: "{{ findings | map(attribute='message') }}"
```

### Checking the live state of MCT clusters

The checks above look at configuration. `fastiron_mct_health` checks what `show cluster` reports:
that each cluster is deployed, the peer is reachable over the keep-alive VLAN, the cluster control
protocol (CCP) session with the peer is up with every member VLAN active, and each client is up.
Given the running configuration, it also reports deployed clusters and clients that `show cluster`
doesn't show.
`fastiron_mct_health_compare` checks both peers' `show cluster` together, and reports split-brain:
both switches showing a client as `Master`, each serving it on its own.

```yaml
- name: Get the live state of the MCT clusters
  ansible.netcommon.cli_command:
    command: show cluster
  register: cluster

- name: Fail when an MCT cluster isn't working
  vars:
    findings: "{{ cluster.stdout | glitchwrks.fastiron.fastiron_mct_health(running.stdout) }}"
  ansible.builtin.assert:
    that: findings | selectattr('severity', '==', 'error') | list | length == 0
    fail_msg: "{{ findings | map(attribute='message') }}"
```

The parser was written against a healthy cluster's output. Fields it can't find are reported as
warnings rather than passed silently.

### Managing VLANs, LAGs and MCT from a model

A model describes a switch's VLANs, LAGs, VE addresses and MCT clusters, in the same shape
`fastiron_parse_config` returns, so a parsed running configuration is a valid starting point.
`ansible-doc -t filter glitchwrks.fastiron.fastiron_model_config` describes the format.

```yaml
- name: Get the running configuration
  ansible.netcommon.cli_command:
    command: show running-config
  register: running

- name: Check the model before changing anything
  ansible.builtin.assert:
    that: model | glitchwrks.fastiron.fastiron_model_config | glitchwrks.fastiron.fastiron_mct_audit | length == 0

- name: Add what the switch is missing
  vars:
    commands: "{{ running.stdout | glitchwrks.fastiron.fastiron_config_commands(model) }}"
  ansible.netcommon.cli_config:
    config: "{{ commands | join('\n') }}"
    diff_match: none
  when: commands | length > 0

- name: Report what still differs
  ansible.builtin.debug:
    msg: "{{ running.stdout | glitchwrks.fastiron.fastiron_config_drift(model) | map(attribute='message') }}"
```

Changes are additive: missing VLANs, ports, VE addresses, LAGs, clusters and clients are added,
and anything undeployed is deployed. Removing something, or changing a LAG or cluster in place,
usually means undeploying it first, so those differences are only reported, for you to fix by hand.

## Development

Run the tests with [`ansible-test`](https://docs.ansible.com/ansible-core/devel/dev_guide/testing.html)
from a checkout at `ansible_collections/glitchwrks/fastiron`, with `ansible.netcommon` and
`ansible.utils` installed alongside:

```sh
ansible-test sanity --docker default
ansible-test units --docker default
```

User-facing changes need a changelog fragment in `changelogs/fragments/`. See the
[antsibull-changelog documentation](https://ansible.readthedocs.io/projects/antsibull-changelog/changelogs/).

## Releasing

Releases are published to Ansible Galaxy by the `release` workflow when a version tag is pushed.

1. Make sure every user-facing change since the last release has a fragment in
   `changelogs/fragments/`.
2. Set the new version in `galaxy.yml`, following [semantic versioning](https://semver.org/).
3. Generate the release notes, which updates `CHANGELOG.md` and removes the fragments:
   ```sh
   antsibull-changelog release
   ```
4. Commit, and merge to `master` once CI passes.
5. Tag the merged commit with the version, without a `v` prefix, and push the tag:
   ```sh
   git tag 0.2.0
   git push origin 0.2.0
   ```

The workflow checks that the tag matches `galaxy.yml`, builds the collection, publishes it to
Galaxy and creates a GitHub release with the built archive attached. Publishing needs a Galaxy API
token for the `glitchwrks` namespace, stored as the `GALAXY_API_KEY` repository secret.

A version can only be published to Galaxy once. If a release goes wrong, fix it and release the
next patch version.

## License

GNU General Public License v3.0 or later. See [LICENSE](LICENSE).
