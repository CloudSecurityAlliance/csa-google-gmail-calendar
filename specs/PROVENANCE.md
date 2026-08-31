# Upstream Discovery document snapshots

Fetched 2026-08-31 from Google's Discovery service. These are snapshots of someone
else's moving target; re-fetch and diff before trusting them. Google revises these
documents continuously - the `revision` field is the upstream date stamp, and both
were revised within the last week of this fetch.

Google publishes **Discovery documents**, not OpenAPI. Unlike Zendesk's specs, these
are linked from the official documentation and are the same artifact the Google client
libraries are generated from, so they are authoritative rather than best-effort.

| file | id | revision | source URL | sha256 | bytes |
|---|---|---|---|---|---|
| `gmail-v1-discovery.json` | `gmail:v1` | 20260824 | https://gmail.googleapis.com/$discovery/rest?version=v1 | `02f12287887f5bce…` | 217687 |
| `calendar-v3-discovery.json` | `calendar:v3` | 20260826 | https://www.googleapis.com/discovery/v1/apis/calendar/v3/rest | `f306783cdda96a2b…` | 169810 |

Full digests:

```
02f12287887f5bce1782645ef2104046b2d06492981e843bd39959456fa0f931  gmail-v1-discovery.json
f306783cdda96a2ba759428aa467ed5bcc44a538c2d3abba3bebb957a60d3f70  calendar-v3-discovery.json
```
