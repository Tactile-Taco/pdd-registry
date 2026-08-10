# Ambiguity Log

- **Tool surface vs REST re-export (v1.0.0).** The bundle's `provides` are
  the MCP JSON-RPC tool/resource schemas — NOT a re-export of pdd-registry's
  REST handshake schemas. The MCP server is a different interface with its
  own contract; re-exporting the REST schemas would leave the MCP-specific
  surface (submission.check, skills resources, version manifest)
  unsealed. pdd-registry-mcp depends_on pdd-registry (it implements a
  client interface to its search/verify surface); the dependency is a
  semantic edge, not a schema inheritance.
- **Read-only Phase A.** Publishing is intentionally NOT an MCP tool in
  1.0.0: the surface is read-only, so no bearer token is needed at the MCP
  layer and the phased authz plan holds (Phase B adds PDD_ADMIN_TOKEN-gated
  mint tools). Agents publish via `pdd publish` (REST /publish), then can
  use registry.submission.check beforehand and registry.evidence.verify
  afterwards.
- **"Surface freshness" wording.** S-004 is the S-008 analogue for this
  bundle: the served surface is generated from the sealed bundle, and the
  server refuses to serve when the on-disk digest drifts from the latest
  admission (keyless staleness gate on the bundle itself).
- **Candidate purity.** The attested candidate (mcp_core) receives bundle
  contents, evidence contents, and registry results via caller-supplied
  arguments (mirroring the pdd-registry candidate's caller-supplied
  catalog): filesystem reads, network fetches, and the registry HTTP client
  are deployment surface (src/registry_mcp.py + the /mcp route).
