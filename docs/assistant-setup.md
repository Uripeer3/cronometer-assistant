# Connect an existing assistant

## Local compatibility prototype

This milestone supplies a standard stdio MCP server. A local MCP-capable host can launch it with this configuration, replacing `/absolute/path/cronometer-assistant` with its checkout path:

```json
{
  "mcpServers": {
    "meal-assistant-demo": {
      "command": "uv",
      "args": [
        "--directory", "/absolute/path/cronometer-assistant",
        "run", "--locked", "--extra", "mcp",
        "cronometer-assistant", "serve",
        "--db", "/absolute/path/cronometer-assistant/data/demo.sqlite"
      ]
    }
  }
}
```

This is a generic local MCP configuration, not a claim that the ChatGPT phone app accepts local stdio servers. Specific hosts may use a different settings format. Do not expose this process to the internet using an unauthenticated proxy.

Example request:

> Help me record a meal in the demo diary. אני מכין אורז ויוגורט. Ask about anything consequential before logging it, and keep it as a draft while I am cooking.

The five default foods have synthetic profiles: cooked rice, dry rice, egg, yogurt, and olive oil. They only demonstrate workflow and ranking. A real meal's nutrition must come from verified Cronometer entries or labels after the live adapter is enabled.

## Phone/ChatGPT gate

Current OpenAI documentation describes restrictions on custom MCP write access. Having a ChatGPT subscription or access to Live voice does not prove this custom server can be used on the phone. Verify this account before buying another plan or implementing public hosting.

The first check is whether the account permits a custom MCP connection with write tools. If the Connection form offers **Tunnel**, use the [private tunnel setup guide](tunnel-setup.md) to connect the existing stdio server. This release supplies a foreground tunnel launcher, rather than a public endpoint or hosted OAuth service. The phone voice test is still required.

Test English, Hebrew, code-switching, one follow-up, and an attached nutrition-table photo. Count tool approval taps and time to complete a draft. Never describe success in a simulated diary as an entry in the real Cronometer account.

References checked 16 November 2026:

- [OpenAI custom MCP connections](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt)
- [OpenAI Voice](https://help.openai.com/en/articles/20001274-chatgpt-voice)
- [Google custom apps and current availability](https://support.google.com/gemini/answer/17209137)

## Authentication is a separate milestone

Do not paste a Cronometer password, session token, or two-factor secret into an assistant conversation. The upstream library currently relies on environment credentials and cached sessions; it is not an authentication UI. We must review and adapt that boundary before enabling the live catalog in the assistant.

## Package photos and public review

Retain product photos privately with their product/version even if the user does
not want public submission. Current MCP tools do not ingest or archive images; a
host must supply a durable retained-file reference or verified local archive.
Never say a chat attachment was filed, attached to Cronometer, or submitted merely
because it appeared in the conversation. These are three different outcomes.

Once the label and saved private food are verified, offer public submission once
unless the user already requested or declined it. Preserve that choice while
collecting missing barcode/front/label evidence. Do not repeatedly request the
same approval. The current public-submission tool only returns a checklist; use
the authenticated Publish Food UI until actual upload/submission support exists.

The implementation plan specifies durable evidence, attachment deduplication,
choice persistence, upload receipts and interrupted-submission recovery. Test
photos-retained-but-declined and already-requested-but-barcode-missing separately.
