# Security

This is an early local-first tool. Passing tests and scanners does not guarantee arbitrary session text contains no confidential information. Do not publish generated reports or state.

If you find a vulnerability or suspected secret, do not open a public issue with the value or source data. Use the repository's private vulnerability reporting facility if available, or contact the maintainer through an existing private channel. Provide a synthetic reproduction and affected version. Never send real credentials; revoke/rotate any credential that may have been exposed.

The project does not offer a security response SLA. Start with the latest reviewed source and keep the repository private until you have reviewed the exact tree and history you intend to publish. Private visibility is not a replacement for secret hygiene.

All shipped fixtures are synthetic. No scanner exceptions are installed. Investigate every new finding before publishing.
