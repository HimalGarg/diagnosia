# Meko setup

Meko gives agents shared context. Here it does three things: it answers searches of approved workflow
knowledge, it keeps a note when a handoff happens, and it indexes [patient packs](PATIENT_PACKS.md). It's
reached only through its documented MCP server, `https://mcp.mekodata.ai/mcp`.

## Connect

1. Sign up at [cloud.mekodata.ai](https://cloud.mekodata.ai), and create a datapack named for
   Diagnosia's demo. Don't use a personal one.
2. Create an API key under Settings > API Keys. It's shown once.
3. Add to `.env`:

   ```dotenv
   ENABLE_MEKO_INTEGRATION=1
   MEKO_API_KEY=
   MEKO_DATAPACK_ID=
   ```

4. Check under CareFlow, Integration status. Rotate a key by creating a new one, updating `.env`,
   restarting, and revoking the old one in the portal.

## Tools used

| Tool | Used for |
|---|---|
| `knowledgebase_search` | Approved guidance, and context for a handoff |
| `knowledgebase_upload_document`, `knowledgebase_document_status` | Loading the knowledge base |
| `memory_add`, `memory_search` | A note that a handoff happened, and reviewed conventions |
| `datapack_describe` | Checking the datapack can be reached |

Tool names and schemas are read from the server at run time. A tool it doesn't offer, an argument it doesn't accept, a missing required argument or a wrong type is refused before anything is sent, so retired arguments such as `scope` can't go out. Most calls need a `conversation_id`, so the client creates one per process and writes nothing into it. Meko reports most failures as an ordinary reply holding `error`, and these count as failures. The shared datapack comes from `.env`, and a patient's comes from SQLite after the access gate, so no request can choose one. The key is sent only to the MCP server, with the `User-Agent` header Meko asks for, and never to an upload URL.

## Handoffs

For each real handoff, the worker searches the knowledge base for what's relevant to the receiving tool, then saves a note built from fixed text. The note's reference goes to YugabyteDB, moving the handoff from `recorded` to `context_shared`. If Meko is down, the handoff stays queued. Context is never invented, and an unavailable search is never treated as finding nothing.

## What goes in

**Yes:** the documents in the next section, and handoff notes, which are built from fixed text and
module names only.

**Patient packs only, with consent, and only on made-up patients:** finished clinical records. See
[PATIENT_PACKS.md](PATIENT_PACKS.md).

**Never:** names, dates of birth, contact details, images, or keys, and clinical text anywhere outside a
patient's own pack. Meko's context search can return conversations from every member of a datapack, so
conversations aren't used for anything about a case.

## Loading knowledge

Automated. See [MEKO_KNOWLEDGE_INGESTION.md](MEKO_KNOWLEDGE_INGESTION.md).

## How it fails

Timeouts are 15 seconds, with one retry. Any failure comes back as "unavailable", never as an empty
result, and handoff notes stay queued. Meko's result is supplemental: it's never proof of identity,
consent, approval or clinical truth. Its text isn't fed into clinical prompts. A pack search only says
which records to read, and those are read again from SQLite.

The free plan allows 1,000 conversations and 10,000 retrievals a month. Check the current limits.
