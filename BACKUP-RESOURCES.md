# Backup resources

Exposure, access model, data classification and accepted risks are in
[`SECURITY-RESOURCES.md`](SECURITY-RESOURCES.md). This file answers only the backup question —
and this server is the one of CSA's four that persists **content** as well as credentials, which
changes the answer.

## Two kinds of persistent state, and they need opposite treatment

### Credentials — retention: zero copies

| File | Recoverable? |
|---|---|
| `~/.csa_google_gmail_calendar/client_secret.json` (or `CSA_GGC_CLIENT_SECRETS`) | Yes — re-run the CSA setup script. The authority is the Google Cloud project `csa-gmail-calendar-mcp`, not this file |
| `~/.csa_google_gmail_calendar/token.json` (or `CSA_GGC_TOKEN_PATH`) | Yes — `authenticate` |

Losing either costs one command, so a backup saves you from nothing. Copying either puts a
credential somewhere nothing is watching. **Any system that has already swept this directory is
a disclosure to assess, not a safety net.**

**Measured on the authoring machine, 2026-10-02:**

```
client_secret.json                            owner_only=True
client_secret.json.20260929-pre-bom-fix.bak   owner_only=False    <-- holds a live client secret
token.json                                    owner_only=True
```

`(I)`-marked inherited ACEs are the reason: the current files were narrowed by this repo's
`icacls` hardening, and the `.bak` predates it, so it still carries whatever the containing
directory grants — on that machine, read-and-execute to a sandbox-users group. **Note that
`ls -l` cannot see this**: every file there renders `-rw-r--r--` in Git Bash, which is not the
permission that governs access on Windows, so the hardened and unhardened files look identical.

That `.bak` is safe to delete and should be. It was written by an older CSA setup script during a
one-off encoding fix; current scripts no longer create it. **Rotation is by resetting the secret
in the Google Cloud Console — never by copying a new file beside the old one.** The same file
exists for the sibling server, and csa-google-workspace
[`BACKUP-RESOURCES.md`](https://github.com/CloudSecurityAlliance/csa-google-workspace/blob/main/BACKUP-RESOURCES.md)
records the identical finding with its `icacls` output.

### Downloaded content — retention: the operator's call, and nobody else can make it

`CSA_GGC_DOWNLOAD_DIR` is where attachments fetched from a mailbox land. That makes this the only
one of the four servers whose on-disk state includes **other people's email content** rather than
only CSA's own credentials.

Three consequences worth stating, because none of them is the code's to solve:

1. **It is not re-obtainable in the trivial sense.** The message is still in Gmail, so nothing is
   *lost* — but the directory accumulates whatever a model was asked to fetch, and nothing prunes
   it. A download directory is a growing extract of a mailbox.
2. **It is a user-chosen path, so it can be anywhere** — including inside OneDrive, Dropbox or
   iCloud, which would copy mailbox content into a vendor's storage and onto every device. The
   code cannot detect that and should not refuse it.
3. **It is adjacent to the rule in CINO-Platform-Engineering
   [`DATA-BOUNDARIES.md`](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/DATA-BOUNDARIES.md)** —
   *"no customer or member data, ever. Not a corpus, not a sample, not an illustrative excerpt"*
   — which is about repositories, not laptops. A download directory is the mechanism by which
   that data most plausibly reaches a place it could then be committed from. Worth knowing when
   choosing the path.

**The right retention for that directory is whatever the operator decides and writes down.** This
project should not pick for them, but it should say the question exists, which nothing did before.

## The separation that makes the download directory safe at all

`CSA_GGC_DOWNLOAD_DIR` **does not** default to `CSA_GGC_ATTACH_DIR`, does not default to a
subdirectory of it, and the upload side's rule stays *"only files under `CSA_GGC_ATTACH_DIR` may
be read and put on the wire"* (default `~/CSA-Uploads`).

`_attachments.py` says why: with a single directory, a downloaded file becomes re-uploadable —
content that arrived from outside, **wearing a name the user trusts.** A confused-deputy path,
closed by separating the directions.

So the two directories have genuinely different retention answers: the upload directory is
material the operator chose to send, and the download directory is material someone else sent
them. Do not merge them to simplify a backup policy.

## A token can be present, valid-looking, and dead

A token issued by an OAuth client whose Google Cloud project has been deleted cannot be refreshed
and **cannot be repaired by logging in again.** So "restore the token" is never the remedy —
replace the client, then authenticate. csa-google-workspace#510 and #495 are that failure in the
sibling, where `auth_status` reported `ready` while every call failed.

## Elsewhere

- Exposure surface, access model, data classification, accepted risks:
  [`SECURITY-RESOURCES.md`](SECURITY-RESOURCES.md).
- Dependencies and what their change looks like:
  [`OPERATIONAL-RESOURCES.md`](OPERATIONAL-RESOURCES.md).
- Why `ls -l` and `os.chmod` cannot be trusted on Windows:
  [`POSIX-AND-WINDOWS.md`](https://github.com/CloudSecurityAlliance-Internal/CINO-Platform-Engineering/blob/main/POSIX-AND-WINDOWS.md).
