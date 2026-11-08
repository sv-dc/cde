# Cindy — locked decisions

Cindy is a common data environment (CDE) for construction projects. The files stay in
each company's own drive or server. Cindy keeps the **record**: what exists, who
published which revision, in what state, and who holds the bytes.

**How to use this file.** Entries are permanent. To change a decision, add a new entry
that supersedes the old one and name the old ID. Never edit an old entry, and never
silently drop the consequence the owner accepted at the time.

Status: answers locked 7 Nov 2026. One open number (see Open questions).

---

## A — Model

**A1 — The container is the unit, not the file.**
A container is one row of the master information delivery plan (MIDP): a model, a
drawing, a document. It has a number, a title, an originator, a type, a parent, its own
attributes, and a state. The same revision of a file can be held by several
stakeholders; the container is what stays constant while file names drift.

**A2 — Files are bytes behind revisions.**
A revision records: content hash, size, media type, the file name it arrived under, the
author, the time. A revision can have many holders (party, path, verified-at). A file
name is an attribute of a revision, never an identity.

**A3 — Immutable revisions.**
Nothing is ever overwritten. A new revision supersedes the old one; both stay in the
record forever. Two competing revisions from the same parent are both visible and a
human marks which one supersedes the other.

**A4 — Typed attributes, plus one free-text pocket.**
Each project defines its attribute set once: name, type, allowed values, and which are
required for which container type. Containers carry values. Every container also has
one free-text pocket so nothing is ever blocked by a missing definition.
Reason: typed fields are indexed (one lookup, not a full scan) and export as a compact
table that an AI reads identically every time. Free-form keys produce `Rev`, `rev`,
`REV.` and make both filters and AI answers unreliable.

**A5 — Simple naming.**
A container needs a number and a title. Number is free text and unique within the
project. ISO 19650 naming is optional: a project template may require the parts, in
which case Cindy assembles and validates the name from them.

**A6 — Hierarchy and delegation.**
Containers form a tree (project → work package or discipline → type → container). The
project information manager owns the tree and may delegate a subtree to an originator.
A delegated party may add and edit containers inside its subtree and may not reach
outside it. Ownership of the parent stays with the delegator.

**A7 — Supersede, withdraw, archive.**
A revision is superseded by a later one, or withdrawn with a reason. A container is
archived when the project closes. Nothing is deleted (see B6).

---

## B — Storage, transport, retention

**B1 — Published revisions live in each party's own storage.**
Each party has a project folder inside the drive or server it already pays for
(OneDrive, SharePoint, a NAS share). Published revisions land there as ordinary files.

**B2 — Cindy carries the bytes.**
Direct connection first (same network, or a direct internet connection between the two
agents). Our hub relays only when a direct connection cannot be made.

**B3 — Relay is metered, direct is free.**
Direct transfers cost us nothing, so they are the default and are faster on the same
ISP. Relay traffic counts against a per-project allowance; overage is billed.
Reason accepted: a flat fee with unlimited relay makes one large project unprofitable.

**B4 — v1 needs no storage-vendor API.**
The agent watches a local folder, so OneDrive, SharePoint and an SMB share look
identical to it. Microsoft Graph and Google Drive connectors are only needed to index
files that live *outside* the Cindy project folder, which v1 does not do.

**B5 — The hub holds no files.**
Metadata, membership, permissions, audit log and the relay's transient buffer. A
published file is never stored on the hub.

**B6 — Nothing is deleted.**
Withdrawal is a state with a reason. Archive is permanent. At project close the client
exports the complete project, verified, into its own storage.

**B7 — Replication is a consequence of visibility.**
What a party is allowed to see is replicated into that party's folder. Replicas are
identical by hash and can repair each other; a missing or corrupt copy is restored from
any holder.

**B8 — Short paths, because real projects already hit the Windows limit.**
The deepest path in our own archive is 253 characters (PRO-148-PMH), seven short of the
260-character Windows limit that many tools still enforce. So the project folder gets one
short name, never a deep prefix, and the agent must handle long paths and never append
more than one level of its own.

**B9 — Do not sell storage savings.**
Measured on our own archive: byte-identical repeats are only about 8% of the bytes
(18XX-CS 8.1%, BIG 8.1%), because every re-export differs. Deduplication is a
correctness feature (one blob, one hash, many holders), not a saving. The value is
finding the right revision and proving what was built.


---

## C — States, gates, visibility

**C1 — Four states on the container.**
Work in progress, shared, published, archived. State is one attribute of the container,
not the file's location.

**C2 — Two signed gates.**
The originator's lead declares a revision *checked*; the project information manager
*authorises* publication. Each step is signed by that person's key and logged.
Reason accepted: this is the shape a client's BIM manager will accept at audit.

**C3 — Hiding can only reduce visibility, never grant it.**
The state sets the default: a work-in-progress container never leaves the originator. On
top of that, a user may mark any container, folder or file as not visible to other
parties. Nothing may make a work-in-progress container visible to another party. Every
hide is logged and reportable per party.
Consequence accepted: someone can hide a published drawing a party needs; the per-party
visibility report is the answer to "why didn't Contractor B receive A-101?".

**C4 — Hiding is inherited and cannot be reopened below.**
Hiding a container hides everything under it. A child cannot re-open what a parent
closed.

**C5 — Hiding is done in the hub, after publishing.**
The desktop side only publishes: the user picks parent folders and Cindy lists what is
new or changed. Which subfolders and files inside those folders are not accessible to
other parties is chosen in the web app, on the published content, by whoever owns it.
Effective immediately on the next sync of the affected party.
Consequence accepted: the originator can withhold part of its own published set, which
is the point, and the information manager can see and override every hide.


---

## D — Clients

**D1 — Desktop is a plain folder, not an application.**
A project folder in the drive the party already syncs, a tray agent, and a right-click
"publish revision to project" in Explorer. No custom file explorer, no Windows virtual
folder with placeholder files, no shell namespace extension.
Reason accepted: those break on Windows updates and get flagged by antivirus, and the
owner does not want another file browser.

**D2 — The web app is the project brain.**
Container tree and lists, attributes, states, approvals, transmittals, audit, visibility
report, and previews of PDF and images. DWG and IFC open in the party's own software. An
in-browser DWG preview needs a converter licence and is priced before it is promised.

**D3 — No agent, no problem.**
A party without the agent works in the browser: view the tree, download a file, upload a
revision. Same permissions, same audit.

**D4 — The desktop app is a publish picker, not a browser.**
The user opens it, selects one or more parent folders, and Cindy walks them and lists
what is new or changed since the last publish, by content hash. The user assigns each
file to a container and publishes. It never navigates the project tree, so it never
becomes the second file explorer the owner does not want.


---

## E — Identity and trust

**E1 — Invite link plus device key.**
An invitation carries the project and the role. Accepting it creates a key on that
device; the hub records the public half. Every publish and every approval is signed, so
the record shows who did what without a password and without a tenant administrator.

**E2 — Revocation is a re-issue.**
The project admin revokes a device key and issues a new invitation. One audit line, no
data loss.

**E3 — Sign-in can be added later.**
Microsoft or Google sign-in can sit on top for people who prefer it; the keys underneath
do not change.

---

## F — Commercial and hosting

**F1 — Flat fee per project, unlimited users.**
Revenue does not depend on headcount, which suits projects where dozens of people read
and few publish.

**F2 — One hub, many projects.**
We host a single hub on the company NAS with projects isolated from each other. A
self-hosted copy for a client is a later deployment, not a different product.

**F3 — Our cost exposure is bandwidth only.**
Storage is the clients'; metadata is kilobytes. Relay traffic is the one variable cost
and is bounded by B3.

**F4 — Sizing benchmark, measured on our own archive (corrected).**
Top-level folders in `S:\99-Archived` are **clients**, not projects: 27 clients, 365
second-level folders, 260,086 files, about 9 TB.
The realistic deliverables-only benchmark is **18XX-CS / 2006-CS-AL SADER 2A**: 10,398
files (5,592 PDF, 3,426 DWG), 15.6 GB, 9 folder levels, 131-character deepest path.
Largest single project by file count is **RG / Redipods** (10,235 files, 7,673 PDF, 664
`.msg` emails) where the project *is* the client folder. Deepest real project is
**PRO-148-PMH / BA VI LODGE** at 11 levels and a 253-character path.
Projects above 40,000 files are working files, libraries and point clouds
(001-VR3D 53,078; 00-RAW PC 56,704 at 1.5 TB), not CDE content.
Design point: 5,000 containers and 50,000 revisions per project, metadata in a single
PostgreSQL database on the NAS. A 10,000-file, 15 GB project is the normal case.

---

## Open questions

None.
