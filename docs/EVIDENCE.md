# Evidence — how one real project stores versions

Measured on `S:\99-Archived\BIG` (KAFD), read-only, 7 Nov 2026. Metadata for all files;
content hash (SHA-256) for every file at or below 64 MB.

## The project

| | |
|---|---|
| Files | 8,765 |
| Size | 130.3 GB |
| Hashed (≤64 MB) | 8,497 files, 29.9 GB |
| Not hashed (>64 MB models) | 268 files, ~100 GB |
| Distinct file names | 5,593 |
| Stage folders | `0-PROJECT DOCUMENTATION`, `1-WORK IN PROGRESS`, `2-SHARED` |
| Week folders | W01–W21 |

## The stage folders are decoration

| Stage | Files | Bytes |
|---|---|---|
| 0-PROJECT DOCUMENTATION | 1,406 | 3.4 GB |
| 1-WORK IN PROGRESS | **14** | 0.4 GB |
| 2-SHARED | 7,077 | 26.1 GB |

Work does not pass through work-in-progress; it lands straight in SHARED. The state
names exist, the workflow does not.

## Where the waste actually is

| Kind | Names | Bytes beyond the first copy | Removable by hash? |
|---|---|---|---|
| Same name, byte-identical copies | 1,058 | 5.85 GB | Yes |
| Same name, different content | **585** | **16.97 GB** | **No** |

The repeated identical files are fonts (each face 20–22 times), logos, `.lst` and `.rar`
bundles. The 585 names that differ are the drawings: every copy is a fresh export.

## The drawing case

1,087 drawing names repeat, in 3,094 copies.

| File | Copies | Distinct contents | Week folders |
|---|---|---|---|
| `kafd-701-big-arc-dwg-14000.pdf` | 11 | **11** | W11, W12, W13, W14, W15, W17, W20 |
| `kafd-701-big-arc-dwg-14000.dwg` | 9 | **9** | W12, W13, W14, W15, W17, W20 |
| `kafd-702-big-arc-dwg-14000.pdf` | 9 | 8 | W12, W13, W14, W17, W20 |
| `kafd-mup-big-ktl-rpt-00000.pdf` | 11 | 8 | W15 |

Eleven copies, eleven different files, distinguishable only by the week folder above
them. Nothing in the tree answers "which one did we build from?".

## Cross-stage movement is not the problem

Only 23 content hashes appear in more than one stage. The duplication is *inside*
`2-SHARED`, one folder per week. The problem is not a file moving between states; it is
that each issue is a new folder instead of a new revision.

## What this means for Cindy

- Revision tracking removes the 17 GB of near-copies and answers "which one is current".
  Hash dedupe cannot touch that 17 GB at all.
- Content addressing still earns its keep on the 5.85 GB of fonts and logos — 4.5% of the
  project, and it disappears silently.
- For P2, these 21 week folders are the exact import case: 21 folders of the same
  drawings must become one container with 21 revisions, not 21 containers.
