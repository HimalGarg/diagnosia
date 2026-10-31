# Meko knowledge ingestion

**Upload is automated, through Meko's documented MCP tools. No portal step is needed, and nothing touches Meko's private database.** It hasn't yet run against a real account, because no Meko key is available.

## The supported mechanism

From Meko's MCP reference and knowledge base guide:

1. `knowledgebase_upload_document` takes `filename`, `size`, `content_hash` (SHA-256) and `content_head_base64` (the first 3,072 bytes), and returns a presigned URL valid for 5 minutes. If identical content is already there, it returns `already_uploaded: true`.
2. The file is POSTed to that URL.
3. `knowledgebase_document_status` is polled with the reply's `key`, until `completed`, `failed`, `abandoned` or `oversized`.

Only an owner or a maintainer of the datapack can upload. The free plan's file limit is 5 MB, well above these files.

**One detail isn't published:** the exact shape of the presigned reply. `backend/integrations/knowledge.py` handles a reply with form fields and one without. If the real reply differs, the first live run will fail clearly, and the fix is small.

## What goes in

`backend/integrations/knowledge_base/` holds 9 versioned files, in 8 categories:

| File | Category |
|---|---|
| `module-overview.md` | Module documentation |
| `workflow-rules.md`, `handoff-rules.md` | Workflow instructions |
| `extraction-templates.md` | Document extraction templates |
| `terminology.md` | Approved terminology |
| `referral-workflow.md` | Referral workflow instructions |
| `discharge-workflow.md` | Discharge workflow checklists |
| `technical-conventions.md` | Internal technical knowledge |
| `integration-operations.md` | Integration and operational documentation |

Each file starts with its provenance: an ID, title, category, version, source in this repository, and review status. All describe process, and declare `clinical_content: none`.

## What's refused

Before anything is sent, each file is checked. A file is refused if its provenance is incomplete, if it declares clinical content, or if it holds anything shaped like an email address, phone number, API key, connection string or date of birth. It's refused rather than cleaned, so a person sees why.

## Running it

```bash
.venv/bin/python -m backend.operator_cli meko-ingest --dry-run   # checks the files, sends nothing
.venv/bin/python -m backend.operator_cli meko-ingest
```

Results are kept in `careflow_knowledge`, with each file's version and hash. A file that hasn't changed isn't sent again. To update a file, change it, raise its version, and run this again.

## Removing a document

`knowledgebase_delete_document` needs a `document_id` from a search result, and it's permanent. `MekoClient.delete_document` exists for this, with no automatic caller, because Meko's documentation says to delete only after a person confirms.

## Memory

`backend/integrations/knowledge_base/memory.json` holds 5 reviewed conventions, each with its source and version. `meko-seed-memory` adds them as private memory. Promoting a memory to everyone in a datapack is left to a person in the Meko portal, because `memory_promote` can't be reversed.

Hidden reasoning, patient histories, clinical records, credentials and unreviewed conclusions are never stored.

## Separating tenants

A datapack is Meko's isolation unit, and the datapack ID comes from the server's configuration, never from a request. Use one datapack per environment. Don't put one tenant's private material in a datapack that another can read: a shared knowledge base is for shared material only.
