# glitchwrks\.fastiron Release Notes

**Topics**

- <a href="#v0-1-0">v0\.1\.0</a>
    - <a href="#release-summary">Release Summary</a>
    - <a href="#new-plugins">New Plugins</a>
        - <a href="#cliconf">Cliconf</a>
        - <a href="#filter">Filter</a>
    - <a href="#new-modules">New Modules</a>

<a id="v0-1-0"></a>
## v0\.1\.0

<a id="release-summary"></a>
### Release Summary

Initial release\. Ansible support for Brocade / Ruckus FastIron switches over <code>network\_cli</code>\, tested against ICX 6650 switches running FastIron 08\.0\.30\, with filters that check MCT cluster configurations for mistakes before a failover exposes them\.

<a id="new-plugins"></a>
### New Plugins

<a id="cliconf"></a>
#### Cliconf

* glitchwrks\.fastiron\.fastiron \- Cliconf plugin for Brocade / Ruckus FastIron switches\.

<a id="filter"></a>
#### Filter

* glitchwrks\.fastiron\.fastiron\_config\_commands \- Generate the FastIron commands that bring a switch closer to a model\.
* glitchwrks\.fastiron\.fastiron\_config\_drift \- Compare a FastIron running configuration with a model\.
* glitchwrks\.fastiron\.fastiron\_config\_removals \- Generate the FastIron commands that remove ports a model does not list\.
* glitchwrks\.fastiron\.fastiron\_mct\_audit \- Check a FastIron switch\'s MCT configuration for mistakes\.
* glitchwrks\.fastiron\.fastiron\_mct\_compare \- Check that two FastIron MCT peers agree\.
* glitchwrks\.fastiron\.fastiron\_mct\_health \- Check the live state of a FastIron switch\'s MCT clusters\.
* glitchwrks\.fastiron\.fastiron\_mct\_health\_compare \- Check the live state of two FastIron MCT peers together\.
* glitchwrks\.fastiron\.fastiron\_model\_config \- Validate a FastIron configuration model\.
* glitchwrks\.fastiron\.fastiron\_parse\_cluster\_status \- Parse the output of FastIron\'s show cluster command\.
* glitchwrks\.fastiron\.fastiron\_parse\_config \- Parse a FastIron running configuration\.

<a id="new-modules"></a>
### New Modules

* glitchwrks\.fastiron\.fastiron\_ssh\_keys \- Manage the SSH client public keys of a FastIron switch\.
