# slack-cards installed

1. Restart the gateway: `systemctl --user restart hermes-gateway`.
2. If you skipped the Node dependencies, the plugin installs them on its first start (needs `security.allow_lazy_installs`, on by default).
3. Add the "Slack cards" section from the README to your `SOUL.md`, so the agent uses cards without being asked.
4. Test in a new Slack thread: `@<bot> give me a health card for <service>`.
