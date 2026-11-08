# Cindy — phase plan (v2)

Order, dependencies and the test that ends each phase. No dates: each phase ends when
its test passes, not when a calendar says so.

Changed from v1 of this plan:
- **Removed:** per-vendor storage connectors (OneDrive, Google, Dropbox) from the whole
  plan. With a plain folder and an agent that watches it, all of them are the same
  folder. Vendor APIs return only if we later index files outside the Cindy folder.
- **Moved into P2:** the container *tree* with delegation. Hierarchy cannot be added
  after rows exist without migrating every container and link.
- **Moved into P3:** user-chosen hiding, with the reduce-only rule (C3, C4).
- **Narrowed:** in-browser viewing to PDF and images. DWG and IFC open in the party's own
  software.

---

## P1 — Two machines, two storages, one file

The smallest thing that proves the promise: bytes never sit on our server.

**Delivers:** two agents on two machines, one with a OneDrive folder and one with an SMB
share; one container; a PDF published as revision A then B; hash verification on receipt;
a hub that records the container and the revisions and stores no file.

**Done when:** Company B's machine shows revision B within seconds, the opened file is
byte-identical to the published one, the hub's own storage grows by kilobytes only, and
pulling the network cable leaves both sides working and converging on reconnect.

**Depends on:** B1, B2, B5, A1–A3, E1.

---

## P2 — The container tree

**Delivers:** containers with a number, title, originator, type, parent and typed
attributes from a project template; a tree with subitems; delegation of a subtree to a
party; import of the team's existing Excel MIDP; a web list with filters and the
free-text pocket; the publish picker that takes parent folders and lists new and changed
files by hash.

**Done when:** an existing MIDP spreadsheet is imported into a tree in one action, a
party delegated a work package can add and edit containers inside it, cannot edit
outside it, a 20 GB folder selected in the picker produces a list of only the files that
changed since the last publish, and a filtered list (type = drawing, level 3, state =
shared) answers instantly at 5,000 containers.

**Depends on:** A1, A4, A5, A6, D2, D3.

---

## P3 — States, gates, visibility

**Delivers:** work in progress → shared → published → archived; the two signed gates;
replication driven by visibility; hiding of published subfolders and files chosen in the
web app, which can only reduce visibility; the per-party visibility report; the audit
log.

**Done when:** a work-in-progress container is absent from the other party's folder and
cannot be requested from the hub; a hidden subfolder is absent from the other party's
folder and appears in the visibility report with who hid it and when; an approval shows
the signer, the time and the exact revision hash it covered.

**Depends on:** C1–C4, B7, E1.

---

## P4 — Replicas

**Delivers:** the same revision held by several parties; dedupe by content hash; repair
of a missing or corrupt copy from any other holder.

**Done when:** a revision held by three parties is deleted from one folder and restored
automatically from another, with the hash confirmed afterwards.

**Depends on:** B7, A2.

---

## P5 — Relay

**Delivers:** direct connection attempted first; relay through the hub when it fails;
per-project relay allowance and usage reporting.

**Done when:** two machines behind hostile networks (neither reachable directly) exchange
a 200 MB file through the relay, the direct path is recorded as failed, and the usage
lands in the project's allowance.

**Depends on:** B2, B3.

---

## P6 — Transmittals

**Delivers:** send a set of containers to a party as a transmittal; receipt tracking;
what was sent, when, at which revision hash.

**Done when:** a transmittal of 20 containers is issued, the recipient's app acknowledges
each item, and the record shows any item that was later superseded.

**Depends on:** P3, D2.

---

## P7 — Handover

**Delivers:** a complete, verified export of the project into the client's own storage,
including the audit log and the attribute data.

**Done when:** a client who has never run Cindy can open the handover folder, verify every
file against the record, and read the full history of each container.

**Depends on:** B6, P3, P4.

---

## Not in this plan

- Editing files inside Cindy. Parties edit in their own software; Cindy records what was
  published.
- 3D model federation. Reuses the VDC reader later, as an addition to P6.
- In-browser DWG preview. Needs a converter licence; priced before it is promised.
- Indexing files that live outside the Cindy project folder.
- Mac and Linux agents.
